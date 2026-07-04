"""Tests the three background jobs directly (not through HTTP, since
there's no request involved — these are triggered by a timer, not a
client). Extends the fake harness with .neq(), .not_.is_(), .lte(),
.in_(), and count="exact" support, none of which any single endpoint
needed on its own.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "fake"
os.environ["SUPABASE_JWT_SECRET"] = "fake"

from app.services.escalation import (  # noqa: E402
    compute_effective_urgency,
    run_escalation_job,
    run_overdue_flip_job,
)
from app.services.reminders import run_fire_reminders_job  # noqa: E402


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Not:
    """Supports the .not_.is_(...) chain — a tiny proxy that negates
    whatever filter method gets called on it next."""

    def __init__(self, query):
        self.query = query

    def is_(self, field, value):
        self.query.filters.append(("not_is", field, value))
        return self.query


class FakeQuery:
    def __init__(self, table, store):
        self.table_name, self.store = table, store
        self.filters, self.op, self.payload = [], "select", None
        self.want_single, self.count_mode = False, None

    def select(self, *_columns, count=None, head=None):
        self.op = "select"
        self.count_mode = count
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = row
        return self

    def update(self, row):
        self.op = "update"
        self.payload = row
        return self

    def eq(self, f, v):
        self.filters.append(("eq", f, v))
        return self

    def neq(self, f, v):
        self.filters.append(("neq", f, v))
        return self

    def is_(self, f, v):
        self.filters.append(("is", f, v))
        return self

    def lt(self, f, v):
        self.filters.append(("lt", f, v))
        return self

    def lte(self, f, v):
        self.filters.append(("lte", f, v))
        return self

    def in_(self, f, values):
        self.filters.append(("in", f, values))
        return self

    @property
    def not_(self):
        return _Not(self)

    def maybe_single(self):
        self.want_single = True
        return self

    def _matches(self, row):
        for kind, field, value in self.filters:
            actual = row.get(field)
            if kind == "eq" and str(actual) != str(value):
                return False
            if kind == "neq" and str(actual) == str(value):
                return False
            if kind == "is" and value == "null" and actual is not None:
                return False
            if kind == "not_is" and value == "null" and actual is None:
                return False
            if kind == "lt" and not (str(actual) < str(value)):
                return False
            if kind == "lte" and not (str(actual) <= str(value)):
                return False
            if kind == "in" and actual not in value:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.op == "insert":
            new_row = {
                "id": str(uuid.uuid4()),
                "nudge_count": 1,
                "interaction_type": None,
                **self.payload,
            }
            rows.append(new_row)
            return FakeResult([new_row])
        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self.payload)
            return FakeResult(matched)
        matched = [r for r in rows if self._matches(r)]
        if self.want_single:
            return FakeResult(matched[0] if matched else None)
        if self.count_mode:
            return FakeResult(matched, count=len(matched))
        return FakeResult(matched)


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(name, self.store)


def iso(dt):
    return dt.isoformat()


now = datetime.now(timezone.utc)

print("=" * 60)
print("TEST 1: compute_effective_urgency — pure function, threshold by threshold")
cases = [
    (now - timedelta(hours=1), 2, 5, "overdue -> forced to 5"),
    (now + timedelta(hours=3), 2, 5, "under 6h -> at least 5"),
    (now + timedelta(hours=12), 2, 4, "under 24h -> at least 4"),
    (now + timedelta(hours=48), 2, 4, "under 72h -> at least 4"),
    (now + timedelta(hours=100), 2, 3, "under 168h -> at least 3"),
    (now + timedelta(hours=200), 2, 2, "over 7 days -> unchanged"),
    (now + timedelta(hours=3), 5, 5, "never lowers a higher base_urgency"),
]
for due, base, expected, description in cases:
    result = compute_effective_urgency(due, base)
    status_str = "OK" if result == expected else "FAIL"
    print(f"  [{status_str}] {description}: got {result}, expected {expected}")
    assert result == expected, description

print("\nTEST 2: run_escalation_job — only updates tasks that actually changed")
db = FakeSupabase()
db.store["tasks"] = [
    {"id": "t1", "base_urgency": 2, "urgency": 2, "due_at": iso(now + timedelta(hours=3)),
     "status": "later", "archived_at": None, "escalation_enabled": True},
    {"id": "t2", "base_urgency": 3, "urgency": 3, "due_at": iso(now + timedelta(hours=200)),
     "status": "later", "archived_at": None, "escalation_enabled": True},
    {"id": "t3", "base_urgency": 2, "urgency": 2, "due_at": iso(now + timedelta(hours=3)),
     "status": "later", "archived_at": None, "escalation_enabled": False},  # disabled — should be skipped
]
result = run_escalation_job(db)
print(f"  Checked: {result['checked']} (expect 2 — t3 excluded, escalation disabled)")
print(f"  Updated: {result['updated']} (expect 1 — only t1 crosses a threshold)")
assert result["checked"] == 2
assert result["updated"] == 1
t1 = next(t for t in db.store["tasks"] if t["id"] == "t1")
print(f"  t1's urgency is now: {t1['urgency']} (expect 5)")
assert t1["urgency"] == 5

print("\nTEST 3: run_overdue_flip_job")
db2 = FakeSupabase()
db2.store["tasks"] = [
    {"id": "t1", "due_at": iso(now - timedelta(hours=1)), "status": "today", "archived_at": None},
    {"id": "t2", "due_at": iso(now + timedelta(hours=1)), "status": "today", "archived_at": None},
    {"id": "t3", "due_at": iso(now - timedelta(hours=1)), "status": "done", "archived_at": None},  # already done, skip
]
result = run_overdue_flip_job(db2)
print(f"  Flipped to overdue: {result['flipped_to_overdue']} (expect 1 — only t1)")
assert result["flipped_to_overdue"] == 1
t1 = next(t for t in db2.store["tasks"] if t["id"] == "t1")
assert t1["status"] == "overdue"

print("\nTEST 4: run_fire_reminders_job — fires a due reminder, skips a future one")
db3 = FakeSupabase()
db3.store["tasks"] = [{"id": "t1", "user_id": "u1", "label": "Send the PDF", "due_at": iso(now)}]
db3.store["users"] = [{"id": "u1", "fcm_token": None}]  # no token — push should be skipped, not error
db3.store["task_reminder_preferences"] = [
    {"id": "r1", "task_id": "t1", "remind_at": iso(now - timedelta(minutes=5))},  # due — should fire
    {"id": "r2", "task_id": "t1", "remind_at": iso(now + timedelta(hours=1))},    # future — should NOT fire
]
result = run_fire_reminders_job(db3)
print(f"  Checked: {result['checked']} (expect 1 — only r1 is due)")
print(f"  Fired: {result['fired']} (expect 1)")
print(f"  Pushed: {result['pushed']} (expect 0 — no fcm_token, skipped gracefully not an error)")
assert result["checked"] == 1
assert result["fired"] == 1
assert result["pushed"] == 0

print("\nTEST 5: run_fire_reminders_job again — same reminder should NOT re-fire (idempotent)")
result2 = run_fire_reminders_job(db3)
print(f"  Fired this time: {result2['fired']} (expect 0)")
print(f"  Already fired (skipped): {result2['already_fired']} (expect 1)")
assert result2["fired"] == 0
assert result2["already_fired"] == 1

print("\nTEST 6: push_notifications degrades gracefully with zero Firebase config")
import os as _os  # noqa: E402
_os.environ["FCM_SERVICE_ACCOUNT_PATH"] = "./definitely-does-not-exist.json"
_os.environ["FCM_PROJECT_ID"] = ""
from app.services.push_notifications import send_push_notification  # noqa: E402

# The one guarantee that actually matters: this never raises, and always
# returns False when nothing is configured — regardless of exactly which
# internal step is what actually fails (ApplicationDefault() succeeds
# eagerly even with no real gcloud session; the failure genuinely
# happens one step later, inside messaging.send() itself, and gets
# caught there).
result = send_push_notification("some-token", "Test", "Body")
print(f"  Result with zero config: {result} (expect False, and must not raise)")
assert result is False

print("\n" + "=" * 60)
print("ALL TESTS PASSED")

"""Fakes only the Gemini text-generation call. Everything else — the
'since last sent update' timestamp logic, task filtering, join table
linking — runs through the real router code.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, ".")
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "fake"
os.environ["SUPABASE_JWT_SECRET"] = "fake"
os.environ["GEMINI_API_KEY"] = "fake"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.supabase_client import get_supabase  # noqa: E402
from app.core.auth import get_current_user, CurrentUser  # noqa: E402


class FakeResult:
    def __init__(self, data):
        self.data = data


class _Not:
    def __init__(self, query):
        self.query = query

    def is_(self, field, value):
        self.query.filters.append(("not_is", field, value))
        return self.query


class FakeQuery:
    def __init__(self, table, store):
        self.table_name, self.store = table, store
        self.filters, self.op, self.payload = [], "select", None
        self.want_single, self.order_field, self.order_desc = False, None, False
        self.limit_n = None

    def select(self, *_columns, count=None, head=None):
        self.op = "select"
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

    def is_(self, f, v):
        self.filters.append(("is", f, v))
        return self

    def gt(self, f, v):
        self.filters.append(("gt", f, v))
        return self

    def in_(self, f, values):
        self.filters.append(("in", f, values))
        return self

    @property
    def not_(self):
        return _Not(self)

    def order(self, f, desc=False):
        self.order_field, self.order_desc = f, desc
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def maybe_single(self):
        self.want_single = True
        return self

    def _matches(self, row):
        for kind, field, value in self.filters:
            actual = row.get(field)
            if kind == "eq" and str(actual) != str(value):
                return False
            if kind == "is" and value == "null" and actual is not None:
                return False
            if kind == "not_is" and value == "null" and actual is None:
                return False
            if kind == "gt" and not (str(actual) > str(value)):
                return False
            if kind == "in" and actual not in value:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.op == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            new_rows = []
            for payload in payloads:
                new_row = {
                    "id": str(uuid.uuid4()),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "sent_text": None,
                    "sent_at": None,
                    "drive_file_id": None,
                    **payload,
                }
                rows.append(new_row)
                new_rows.append(new_row)
            return FakeResult(new_rows)
        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self.payload)
            return FakeResult(matched)
        matched = [r for r in rows if self._matches(r)]
        if self.order_field:
            matched.sort(key=lambda r: r.get(self.order_field) or "", reverse=self.order_desc)
        if self.limit_n:
            matched = matched[: self.limit_n]
        if self.want_single:
            return FakeResult(matched[0] if matched else None)
        return FakeResult(matched)


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(name, self.store)


fake_db = FakeSupabase()
USER_ID = "11111111-1111-1111-1111-111111111111"
app.dependency_overrides[get_supabase] = lambda: fake_db
app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=USER_ID, email="t@test.com")
client = TestClient(app)

now = datetime.now(timezone.utc)

# Seed an audience and some tasks tagged to it
fake_db.store["audiences"] = [
    {"id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7", "user_id": USER_ID, "name": "Client",
     "tone_notes": "Formal, no jargon.", "created_at": now.isoformat()}
]
fake_db.store["tasks"] = [
    {"id": "9aaf556b-d0cc-4d2f-bec4-1921c07d3188", "user_id": USER_ID, "audience_id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7", "label": "Update brand assets",
     "status": "done", "updated_at": now.isoformat()},
    {"id": "5853dcd0-7973-42ae-a1ba-e7319eb171ff", "user_id": USER_ID, "audience_id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7", "label": "Send capability PDF",
     "status": "today", "updated_at": now.isoformat()},
    {"id": "8cb6886b-5656-40a4-9b04-a57e165a72c7", "user_id": USER_ID, "audience_id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7", "label": "Later-stage task, not actionable yet",
     "status": "later", "updated_at": now.isoformat()},  # should be excluded — status is 'later'
]

print("=" * 60)
print("TEST 1: Generate draft — first ever update, no since-timestamp floor")
with patch("app.routers.status_updates.generate_status_update_draft", return_value="Fake AI draft text."):
    r = client.post("/v1/status-updates/generate", json={"audience_id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7"})
assert r.status_code == 201, r.text
draft = r.json()
update_id = draft["id"]
print(f"  Status: 201, draft_text: {draft['draft_text']!r}")
print(f"  task_ids count: {len(draft['task_ids'])} (expect 2 — t3 excluded, status='later')")
assert len(draft["task_ids"]) == 2
assert "8cb6886b-5656-40a4-9b04-a57e165a72c7" not in draft["task_ids"]

print("\nTEST 2: Generate draft for an audience with zero eligible tasks -> 400")
fake_db.store["audiences"].append(
    {"id": "8dfb2a55-32ec-4f3c-a98b-25dcb98d7b5d", "user_id": USER_ID, "name": "Empty Audience", "tone_notes": None, "created_at": now.isoformat()}
)
r = client.post("/v1/status-updates/generate", json={"audience_id": "8dfb2a55-32ec-4f3c-a98b-25dcb98d7b5d"})
print(f"  Status: {r.status_code} (expect 400)")
assert r.status_code == 400

print("\nTEST 3: Generate draft for a nonexistent audience -> 404")
r = client.post("/v1/status-updates/generate", json={"audience_id": "99999999-9999-9999-9999-999999999999"})
print(f"  Status: {r.status_code} (expect 404)")
assert r.status_code == 404

print("\nTEST 4: Mark as sent")
r = client.patch(f"/v1/status-updates/{update_id}/send", json={"sent_text": "Edited final text."})
assert r.status_code == 200, r.text
sent = r.json()
print(f"  sent_text: {sent['sent_text']!r}")
print(f"  sent_at is set: {sent['sent_at'] is not None}")
assert sent["sent_text"] == "Edited final text."
assert sent["sent_at"] is not None

print("\nTEST 5: Generate a SECOND draft — should only pull tasks updated since the sent_at above")
# t1 and t2 haven't changed since being sent — a fresh task t4 has.
fake_db.store["tasks"].append(
    {"id": "28585de2-7b6b-44b0-852d-c6fc0e1e5505", "user_id": USER_ID, "audience_id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7", "label": "Brand new task after the send",
     "status": "done", "updated_at": (now + timedelta(hours=1)).isoformat()}
)
with patch("app.routers.status_updates.generate_status_update_draft", return_value="Second draft."):
    r = client.post("/v1/status-updates/generate", json={"audience_id": "9fa03b33-a490-4e00-90bf-99a6a249b7d7"})
assert r.status_code == 201, r.text
second_draft = r.json()
print(f"  task_ids in second draft: {second_draft['task_ids']} (expect only the new task)")
assert second_draft["task_ids"] == ["28585de2-7b6b-44b0-852d-c6fc0e1e5505"]

print("\n" + "=" * 60)
print("ALL 5 TESTS PASSED")

"""
Not a permanent file — a one-off test harness to verify the Task CRUD
endpoints actually work end-to-end (auth, validation, routing, response
shaping) without needing real Supabase credentials.

Real Supabase's Python client has a fluent/chainable query builder:
    supabase.table("tasks").select("*").eq("user_id", x).execute()
This fake mimics just enough of that shape to drive the real router code
through its actual paths, storing rows in a plain Python list instead of
Postgres. It is NOT a replacement for testing against the real database —
just a fast way to catch logic bugs before that.
"""
import sys
import uuid
from datetime import datetime, timezone

import jwt

sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

os_environ_patch = {
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_SERVICE_KEY": "fake",
    "SUPABASE_JWT_SECRET": "test-secret",
}
import os  # noqa: E402
for k, v in os_environ_patch.items():
    os.environ[k] = v

from app.main import app  # noqa: E402
from app.db.supabase_client import get_supabase  # noqa: E402
from app.core.auth import get_current_user, CurrentUser  # noqa: E402


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table, store):
        self.table_name = table
        self.store = store
        self.filters = []
        self.op = "select"
        self.payload = None
        self.want_single = False
        self.order_field = None
        self.order_desc = False

    def select(self, *_):
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

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def is_(self, field, value):
        self.filters.append(("is", field, value))
        return self

    def order(self, field, desc=False):
        self.order_field = field
        self.order_desc = desc
        return self

    def maybe_single(self):
        self.want_single = True
        return self

    def _matches(self, row):
        for kind, field, value in self.filters:
            if kind == "eq" and str(row.get(field)) != str(value):
                return False
            if kind == "is" and value == "null" and row.get(field) is not None:
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
                    "user_id": payload.get("user_id"),
                    "urgency": payload.get("base_urgency", 2),
                    "status": "later",
                    "archived_at": None,
                    "calendar_event_id": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
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

        if self.op == "delete":
            matched = [r for r in rows if self._matches(r)]
            self.store[self.table_name] = [r for r in rows if r not in matched]
            return FakeResult(matched)

        # select
        matched = [r for r in rows if self._matches(r)]
        if self.order_field:
            matched.sort(key=lambda r: r.get(self.order_field, 0), reverse=self.order_desc)
        if self.want_single:
            return FakeResult(matched[0] if matched else None)
        return FakeResult(matched)


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(name, self.store)


fake_db = FakeSupabase()
app.dependency_overrides[get_supabase] = lambda: fake_db
app.dependency_overrides[get_current_user] = lambda: CurrentUser(
    user_id="11111111-1111-1111-1111-111111111111", email="test@example.com"
)

client = TestClient(app)

print("=" * 60)
print("TEST 1: Create a task (no due_at)")
r = client.post("/v1/tasks", json={"label": "Send capability PDF to Fred", "base_urgency": 3})
print(f"  Status: {r.status_code} (expect 201)")
assert r.status_code == 201, r.text
task = r.json()
print(f"  Created task id: {task['id']}")
print(f"  urgency == base_urgency: {task['urgency']} == {task['base_urgency']}")
assert task["urgency"] == task["base_urgency"] == 3
task_id = task["id"]

print("\nTEST 2: Create a task WITH due_at — should auto-create 2 reminders")
r = client.post(
    "/v1/tasks",
    json={"label": "Task with deadline", "due_at": "2026-08-01T17:00:00Z"},
)
assert r.status_code == 201, r.text
reminders = fake_db.store.get("task_reminder_preferences", [])
print(f"  Reminders auto-created: {len(reminders)} (expect 2)")
assert len(reminders) == 2

print("\nTEST 3: List tasks")
r = client.get("/v1/tasks")
assert r.status_code == 200, r.text
print(f"  Status: {r.status_code}, tasks returned: {len(r.json()['tasks'])} (expect 2)")
assert len(r.json()["tasks"]) == 2

print("\nTEST 4: Get single task")
r = client.get(f"/v1/tasks/{task_id}")
assert r.status_code == 200, r.text
print(f"  Status: {r.status_code}, label: {r.json()['label']}")

print("\nTEST 5: Get a task that doesn't exist -> 404")
fake_id = str(uuid.uuid4())
r = client.get(f"/v1/tasks/{fake_id}")
print(f"  Status: {r.status_code} (expect 404)")
assert r.status_code == 404

print("\nTEST 6: Partial update (PATCH) — only send label, urgency should NOT change")
r = client.patch(f"/v1/tasks/{task_id}", json={"label": "Updated label"})
assert r.status_code == 200, r.text
updated = r.json()
print(f"  New label: {updated['label']}")
print(f"  Urgency unchanged: {updated['base_urgency']} (expect still 3)")
assert updated["label"] == "Updated label"
assert updated["base_urgency"] == 3

print("\nTEST 7: Invalid base_urgency (6, out of 1-5 range) -> 422")
r = client.post("/v1/tasks", json={"label": "Bad urgency", "base_urgency": 6})
print(f"  Status: {r.status_code} (expect 422 — Pydantic validation)")
assert r.status_code == 422

print("\nTEST 8: Archive then unarchive")
r = client.patch(f"/v1/tasks/{task_id}/archive")
assert r.status_code == 200 and r.json()["archived_at"] is not None
print("  Archived OK")
r = client.patch(f"/v1/tasks/{task_id}/unarchive")
assert r.status_code == 200 and r.json()["archived_at"] is None
print("  Unarchived OK")

print("\nTEST 9: Delete WITHOUT confirmed:true -> 400 (per contract, not 422)")
r = client.request("DELETE", f"/v1/tasks/{task_id}", json={"confirmed": False})
print(f"  Status: {r.status_code} (expect 400)")
assert r.status_code == 400

print("\nTEST 10: Delete WITH confirmed:true -> 204")
r = client.request("DELETE", f"/v1/tasks/{task_id}", json={"confirmed": True})
print(f"  Status: {r.status_code} (expect 204)")
assert r.status_code == 204

print("\nTEST 11: No auth header at all -> 403")
app.dependency_overrides.pop(get_current_user)
r = client.get("/v1/tasks")
print(f"  Status: {r.status_code} (expect 403, matches earlier whoami behavior)")
assert r.status_code == 403

print("\n" + "=" * 60)
print("ALL 11 TESTS PASSED")

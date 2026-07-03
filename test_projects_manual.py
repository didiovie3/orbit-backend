"""Same fake-Supabase approach as test_tasks_manual.py — see that file's
docstring for the full explanation. This one focuses on the cascade
behaviors specific to projects: archiving/unarchiving/deleting a project
should sweep its tasks along with it.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, ".")
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "fake"
os.environ["SUPABASE_JWT_SECRET"] = "fake"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.supabase_client import get_supabase  # noqa: E402
from app.core.auth import get_current_user, CurrentUser  # noqa: E402


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table, store):
        self.table_name, self.store = table, store
        self.filters, self.op, self.payload = [], "select", None
        self.want_single, self.order_field = False, None

    def select(self, *_):
        self.op = "select"; return self
    def insert(self, row):
        self.op = "insert"; self.payload = row; return self
    def update(self, row):
        self.op = "update"; self.payload = row; return self
    def delete(self):
        self.op = "delete"; return self
    def eq(self, f, v):
        self.filters.append(("eq", f, v)); return self
    def is_(self, f, v):
        self.filters.append(("is", f, v)); return self
    def order(self, f, desc=False):
        self.order_field = f; return self
    def maybe_single(self):
        self.want_single = True; return self

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
                    "id": str(uuid.uuid4()), "urgency": payload.get("base_urgency", 2),
                    "status": "later", "archived_at": None, "calendar_event_id": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    **payload,
                }
                rows.append(new_row); new_rows.append(new_row)
            return FakeResult(new_rows)
        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched: r.update(self.payload)
            return FakeResult(matched)
        if self.op == "delete":
            matched = [r for r in rows if self._matches(r)]
            self.store[self.table_name] = [r for r in rows if r not in matched]
            return FakeResult(matched)
        matched = [r for r in rows if self._matches(r)]
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

print("=" * 60)
print("TEST 1: Create a project")
r = client.post("/v1/projects", json={"name": "Atlas Dev", "color": "#E8834A"})
assert r.status_code == 201, r.text
project = r.json()
project_id = project["id"]
print(f"  Created: {project['name']} (id: {project_id})")
assert project["is_unsorted"] is False

print("\nTEST 2: Invalid color format -> 422")
r = client.post("/v1/projects", json={"name": "Bad color", "color": "not-a-hex-color"})
print(f"  Status: {r.status_code} (expect 422)")
assert r.status_code == 422

print("\nTEST 3: Create 2 tasks under the project")
for label in ["Task A", "Task B"]:
    r = client.post("/v1/tasks", json={"label": label, "project_id": project_id})
    assert r.status_code == 201, r.text
tasks_in_project = [t for t in fake_db.store["tasks"] if t["project_id"] == project_id]
print(f"  Tasks created: {len(tasks_in_project)} (expect 2)")
assert len(tasks_in_project) == 2

print("\nTEST 4: Archive the project -> both tasks should archive too")
r = client.patch(f"/v1/projects/{project_id}/archive")
assert r.status_code == 200 and r.json()["archived_at"] is not None
archived_tasks = [t for t in fake_db.store["tasks"] if t["project_id"] == project_id and t["archived_at"]]
print(f"  Project archived. Tasks also archived: {len(archived_tasks)} (expect 2)")
assert len(archived_tasks) == 2

print("\nTEST 5: Unarchive the project -> both tasks should restore too")
r = client.patch(f"/v1/projects/{project_id}/unarchive")
assert r.status_code == 200 and r.json()["archived_at"] is None
still_archived = [t for t in fake_db.store["tasks"] if t["project_id"] == project_id and t["archived_at"]]
print(f"  Project unarchived. Tasks still archived: {len(still_archived)} (expect 0)")
assert len(still_archived) == 0

print("\nTEST 6: List projects (default excludes archived)")
r = client.get("/v1/projects")
assert r.status_code == 200
print(f"  Projects returned: {len(r.json()['projects'])} (expect 1)")

print("\nTEST 7: Delete project WITHOUT confirmed -> 400, tasks untouched")
r = client.request("DELETE", f"/v1/projects/{project_id}", json={"confirmed": False})
print(f"  Status: {r.status_code} (expect 400)")
assert r.status_code == 400
assert len([t for t in fake_db.store["tasks"] if t["project_id"] == project_id]) == 2

print("\nTEST 8: Delete project WITH confirmed:true -> 204, tasks deleted too")
r = client.request("DELETE", f"/v1/projects/{project_id}", json={"confirmed": True})
print(f"  Status: {r.status_code} (expect 204)")
assert r.status_code == 204
remaining_tasks = [t for t in fake_db.store["tasks"] if t["project_id"] == project_id]
print(f"  Tasks remaining under deleted project: {len(remaining_tasks)} (expect 0)")
assert len(remaining_tasks) == 0

print("\n" + "=" * 60)
print("ALL 8 TESTS PASSED")

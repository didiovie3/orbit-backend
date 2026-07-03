"""Same fake-Supabase approach as the other test_*_manual.py files.
This one adds .lt() (less-than filter, for the pagination cursor) and
.limit() support, since notes' list endpoint needs both.
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
        self.want_single, self.order_field, self.order_desc = False, None, False
        self.limit_n = None

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

    def eq(self, f, v):
        self.filters.append(("eq", f, v))
        return self

    def is_(self, f, v):
        self.filters.append(("is", f, v))
        return self

    def lt(self, f, v):
        self.filters.append(("lt", f, v))
        return self

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
            if kind == "eq" and str(row.get(field)) != str(value):
                return False
            if kind == "is" and value == "null" and row.get(field) is not None:
                return False
            if kind == "lt" and not (str(row.get(field)) < str(value)):
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
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "archived_at": None,
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
        matched = [r for r in rows if self._matches(r)]
        if self.order_field:
            matched.sort(key=lambda r: r.get(self.order_field, ""), reverse=self.order_desc)
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

print("=" * 60)
print("TEST 1: Create a note with no title, no project (both optional)")
r = client.post("/v1/notes", json={"content": "Quick sync with Fred about AtlasDev rebrand."})
assert r.status_code == 201, r.text
note = r.json()
note_id = note["id"]
print(f"  Created note id: {note_id}, title: {note['title']}, summary: {note['summary']}")
assert note["title"] is None
assert note["summary"] is None

print("\nTEST 2: Create a note with empty content -> 422")
r = client.post("/v1/notes", json={"content": ""})
print(f"  Status: {r.status_code} (expect 422)")
assert r.status_code == 422

print("\nTEST 3: Get single note")
r = client.get(f"/v1/notes/{note_id}")
assert r.status_code == 200, r.text
print(f"  Retrieved: {r.json()['content'][:30]}...")

print("\nTEST 4: Update note — add a title, content stays the same")
r = client.patch(f"/v1/notes/{note_id}", json={"title": "AtlasDev Brand Refresh"})
assert r.status_code == 200, r.text
updated = r.json()
print(f"  New title: {updated['title']}")
assert updated["content"] == "Quick sync with Fred about AtlasDev rebrand."

print("\nTEST 5: List notes with pagination (limit=2)")
for i in range(3):
    client.post("/v1/notes", json={"content": f"Note number {i}"})
r = client.get("/v1/notes?limit=2")
assert r.status_code == 200, r.text
print(f"  Notes returned: {len(r.json()['notes'])} (expect 2, even though 4 exist)")
assert len(r.json()["notes"]) == 2

print("\nTEST 6: Archive a note")
r = client.patch(f"/v1/notes/{note_id}/archive")
assert r.status_code == 200 and r.json()["archived_at"] is not None
print("  Archived OK")

print("\nTEST 7: List notes (default excludes archived)")
r = client.get("/v1/notes?limit=100")
returned_ids = [n["id"] for n in r.json()["notes"]]
print(f"  Archived note excluded: {note_id not in returned_ids}")
assert note_id not in returned_ids

print("\nTEST 8: List WITH include_archived=true -> archived note shows up")
r = client.get("/v1/notes?limit=100&include_archived=true")
returned_ids = [n["id"] for n in r.json()["notes"]]
print(f"  Archived note included: {note_id in returned_ids}")
assert note_id in returned_ids

print("\nTEST 9: Delete without confirmed -> 400")
r = client.request("DELETE", f"/v1/notes/{note_id}", json={"confirmed": False})
assert r.status_code == 400
print(f"  Status: {r.status_code} (expect 400)")

print("\nTEST 10: Delete with confirmed:true -> 204")
r = client.request("DELETE", f"/v1/notes/{note_id}", json={"confirmed": True})
assert r.status_code == 204
print(f"  Status: {r.status_code} (expect 204)")

print("\n" + "=" * 60)
print("ALL 10 TESTS PASSED")

"""Same fake-Supabase approach as the other test_*_manual.py files.

One thing this test can't verify: the ON DELETE SET NULL behavior on
tasks.audience_id happens in real Postgres, not in our Python code (that's
the whole point — no application-level cleanup needed). Since the fake
database here doesn't simulate real foreign key constraints, deleting an
audience in this test won't actually null out any task's audience_id the
way it will against your real Supabase project. Worth confirming that
part specifically when you test against the real database.
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

    def order(self, f, desc=False):
        self.order_field = f
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
            new_row = {
                "id": str(uuid.uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(),
                **self.payload,
            }
            rows.append(new_row)
            return FakeResult([new_row])
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
print("TEST 1: Create an audience with tone_notes")
r = client.post("/v1/audiences", json={"name": "Client", "tone_notes": "Formal, no jargon."})
assert r.status_code == 201, r.text
audience = r.json()
audience_id = audience["id"]
print(f"  Created: {audience['name']} — tone: {audience['tone_notes']}")

print("\nTEST 2: Create an audience with no tone_notes (optional)")
r = client.post("/v1/audiences", json={"name": "Self"})
assert r.status_code == 201, r.text
print(f"  Created: {r.json()['name']} — tone: {r.json()['tone_notes']} (expect None)")
assert r.json()["tone_notes"] is None

print("\nTEST 3: Empty name -> 422")
r = client.post("/v1/audiences", json={"name": ""})
print(f"  Status: {r.status_code} (expect 422)")
assert r.status_code == 422

print("\nTEST 4: List audiences")
r = client.get("/v1/audiences")
assert r.status_code == 200
print(f"  Audiences returned: {len(r.json()['audiences'])} (expect 2)")
assert len(r.json()["audiences"]) == 2

print("\nTEST 5: Update tone_notes only, name stays the same")
r = client.patch(f"/v1/audiences/{audience_id}", json={"tone_notes": "Updated tone."})
assert r.status_code == 200, r.text
print(f"  Name unchanged: {r.json()['name']}, tone updated: {r.json()['tone_notes']}")
assert r.json()["name"] == "Client"

print("\nTEST 6: Delete without confirmed -> 400")
r = client.request("DELETE", f"/v1/audiences/{audience_id}", json={"confirmed": False})
assert r.status_code == 400
print(f"  Status: {r.status_code} (expect 400)")

print("\nTEST 7: Delete with confirmed:true -> 204")
r = client.request("DELETE", f"/v1/audiences/{audience_id}", json={"confirmed": True})
assert r.status_code == 204
print(f"  Status: {r.status_code} (expect 204)")

print("\nTEST 8: Deleted audience no longer in list")
r = client.get("/v1/audiences")
remaining_ids = [a["id"] for a in r.json()["audiences"]]
print(f"  Deleted audience gone: {audience_id not in remaining_ids}, remaining count: {len(remaining_ids)} (expect 1)")
assert audience_id not in remaining_ids
assert len(remaining_ids) == 1

print("\n" + "=" * 60)
print("ALL 8 TESTS PASSED")

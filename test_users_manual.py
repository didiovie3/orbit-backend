import os
import sys

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
        self.want_single = False

    def select(self, *_columns, count=None, head=None):
        self.op = "select"
        return self

    def update(self, row):
        self.op = "update"
        self.payload = row
        return self

    def eq(self, f, v):
        self.filters.append((f, v))
        return self

    def maybe_single(self):
        self.want_single = True
        return self

    def _matches(self, row):
        return all(str(row.get(f)) == str(v) for f, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self.payload)
            return FakeResult(matched)
        matched = [r for r in rows if self._matches(r)]
        return FakeResult(matched[0] if self.want_single else matched)


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

fake_db.store["users"] = [
    {
        "id": USER_ID,
        "name": "Ovie",
        "today_task_limit": 3,
        "dynamic_escalation_enabled": True,
        "briefs_enabled": True,
        "morning_brief_time": "08:00:00",
        "progress_brief_times": ["13:00:00"],
        "eod_brief_time": "21:00:00",
        "writing_style_notes": None,
        "google_calendar_token": None,
        "fcm_token": None,
        "created_at": "2026-01-01T00:00:00Z",
    }
]

print("=" * 60)
print("TEST 1: GET /v1/users/me — calendar_connected derived correctly as False")
r = client.get("/v1/users/me")
assert r.status_code == 200, r.text
user = r.json()
print(f"  today_task_limit: {user['today_task_limit']}, calendar_connected: {user['calendar_connected']}")
assert user["calendar_connected"] is False
assert "google_calendar_token" not in user  # never leak the raw token

print("\nTEST 2: PATCH /v1/users/me — update task limit and briefs toggle only")
r = client.patch("/v1/users/me", json={"today_task_limit": 5, "briefs_enabled": False})
assert r.status_code == 200, r.text
updated = r.json()
print(f"  today_task_limit: {updated['today_task_limit']} (expect 5)")
print(f"  briefs_enabled: {updated['briefs_enabled']} (expect False)")
print(f"  name unchanged: {updated['name']} (expect Ovie — wasn't in the patch body)")
assert updated["today_task_limit"] == 5
assert updated["briefs_enabled"] is False
assert updated["name"] == "Ovie"

print("\nTEST 3: PATCH with fcm_token — registering a device token")
r = client.patch("/v1/users/me", json={"fcm_token": "fake-device-token-abc123"})
assert r.status_code == 200, r.text
print(f"  fcm_token stored (not returned in response, confirmed via store): {fake_db.store['users'][0]['fcm_token']}")
assert fake_db.store["users"][0]["fcm_token"] == "fake-device-token-abc123"

print("\nTEST 4: calendar_connected becomes True once a token exists")
fake_db.store["users"][0]["google_calendar_token"] = '{"access_token": "fake"}'
r = client.get("/v1/users/me")
assert r.json()["calendar_connected"] is True
print("  calendar_connected: True (matches stored token presence)")

print("\nTEST 5: Invalid today_task_limit (0, out of 1-10 range) -> 422")
r = client.patch("/v1/users/me", json={"today_task_limit": 0})
print(f"  Status: {r.status_code} (expect 422)")
assert r.status_code == 422

print("\n" + "=" * 60)
print("ALL 5 TESTS PASSED")

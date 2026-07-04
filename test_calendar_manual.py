"""Mocks calendar_service's Google-facing functions specifically (token
exchange, Calendar API calls) — same pattern as mocking Gemini elsewhere.
Everything else (token storage/refresh logic, push/pull sync logic,
webhook channel-token verification, the unsorted-bucket landing for
pulled events) runs through the real code.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
        self.want_single = False

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
            if kind == "is" and value == "null" and actual is not None:
                return False
            if kind == "not_is" and value == "null" and actual is None:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.op == "insert":
            new_row = {"id": str(uuid.uuid4()), **self.payload}
            rows.append(new_row)
            return FakeResult([new_row])
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

fake_db.store["users"] = [{"id": USER_ID, "google_calendar_token": None}]
fake_db.store["projects"] = [
    {"id": "proj-unsorted", "owner_id": USER_ID, "is_unsorted": True, "name": "Unsorted"}
]
fake_db.store["tasks"] = []

FAKE_TOKENS = {
    "access_token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    "email": "ovie@gmail.com",
}

print("=" * 60)
print("TEST 1: Connect — stores token JSON with a generated channel_token")
with patch("app.routers.calendar.exchange_auth_code", return_value=FAKE_TOKENS):
    r = client.post("/v1/calendar/connect", json={"auth_code": "fake-code-from-android"})
assert r.status_code == 201, r.text
print(f"  Status: 201, calendar_email: {r.json()['calendar_email']}")
assert r.json()["connected"] is True

stored = json.loads(fake_db.store["users"][0]["google_calendar_token"])
print(f"  Stored access_token: {stored['access_token']}")
print(f"  channel_token was generated: {'channel_token' in stored and len(stored['channel_token']) > 20}")
assert "channel_token" in stored

print("\nTEST 2: Manual sync — push a task, pull a calendar event")
with patch("app.services.calendar_sync.create_calendar_event", return_value="new-event-123") as mock_create, \
     patch("app.services.calendar_sync.update_calendar_event") as mock_update, \
     patch(
         "app.services.calendar_sync.list_calendar_events",
         return_value=[
             {"id": "gcal-event-1", "summary": "Dentist appointment",
              "start": {"dateTime": "2026-08-01T14:00:00Z"}, "status": "confirmed"},
             {"id": "gcal-cancelled", "summary": "Cancelled meeting",
              "start": {"dateTime": "2026-08-02T10:00:00Z"}, "status": "cancelled"},
         ],
     ):
    fake_db.store["tasks"].append(
        {"id": "task1", "user_id": USER_ID, "label": "Send the PDF",
         "due_at": "2026-08-01T17:00:00Z", "calendar_event_id": None, "archived_at": None}
    )
    r = client.post("/v1/calendar/sync")
assert r.status_code == 200, r.text
sync_result = r.json()
print(f"  Pushed: {sync_result['pushed']} (expect 1)")
print(f"  Pulled: {sync_result['pulled']} (expect 1 — the cancelled one should be skipped)")
assert sync_result["pushed"] == 1
assert sync_result["pulled"] == 1
assert mock_create.called

task1 = next(t for t in fake_db.store["tasks"] if t["id"] == "task1")
print(f"  task1 now has calendar_event_id: {task1['calendar_event_id']}")
assert task1["calendar_event_id"] == "new-event-123"

pulled_task = next(t for t in fake_db.store["tasks"] if t.get("calendar_event_id") == "gcal-event-1")
print(f"  Pulled event landed in Unsorted: {pulled_task['project_id'] == 'proj-unsorted'}")
assert pulled_task["project_id"] == "proj-unsorted"

cancelled_landed = any(t.get("calendar_event_id") == "gcal-cancelled" for t in fake_db.store["tasks"])
print(f"  Cancelled event did NOT create a task: {not cancelled_landed}")
assert not cancelled_landed

print("\nTEST 3: Sync again — task1 already has calendar_event_id, should UPDATE not re-create")
with patch("app.services.calendar_sync.create_calendar_event") as mock_create2, \
     patch("app.services.calendar_sync.update_calendar_event") as mock_update2, \
     patch("app.services.calendar_sync.list_calendar_events", return_value=[]):
    r = client.post("/v1/calendar/sync")
assert r.status_code == 200
print(f"  create_calendar_event called again: {mock_create2.called} (expect False)")
print(f"  update_calendar_event called: {mock_update2.called} (expect True)")
assert not mock_create2.called
assert mock_update2.called

print("\nTEST 4: Webhook with the RIGHT channel_token -> triggers a sync")
with patch("app.services.calendar_sync.list_calendar_events", return_value=[]), \
     patch("app.routers.calendar.run_two_way_sync") as mock_sync:
    r = client.post("/v1/calendar/webhook", headers={"X-Goog-Channel-Token": stored["channel_token"]})
assert r.status_code == 200
print(f"  Sync was triggered for the matching user: {mock_sync.called}")
assert mock_sync.called

print("\nTEST 5: Webhook with the WRONG channel_token -> no-op, still 200")
with patch("app.routers.calendar.run_two_way_sync") as mock_sync2:
    r = client.post("/v1/calendar/webhook", headers={"X-Goog-Channel-Token": "not-a-real-token"})
assert r.status_code == 200
print(f"  Status: 200 (Google gets 200 regardless), sync NOT triggered: {not mock_sync2.called}")
assert not mock_sync2.called

print("\nTEST 6: Disconnect — clears the stored token, attempts revoke")
with patch("app.routers.calendar.revoke_token") as mock_revoke:
    r = client.delete("/v1/calendar/disconnect")
assert r.status_code == 204
print(f"  Status: 204, revoke_token was called: {mock_revoke.called}")
assert mock_revoke.called
assert fake_db.store["users"][0]["google_calendar_token"] is None

print("\nTEST 7: Manual sync when NOT connected -> 400")
r = client.post("/v1/calendar/sync")
print(f"  Status: {r.status_code} (expect 400)")
assert r.status_code == 400

print("\n" + "=" * 60)
print("ALL 7 TESTS PASSED")

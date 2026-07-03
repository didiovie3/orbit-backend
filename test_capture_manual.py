"""Fakes the Gemini extraction call specifically (can't reach the real API
from this environment), but drives real request handling, real hierarchy
-> task-row creation, real reminder auto-creation, and real join-table
linking through the actual endpoint code.
"""
import io
import os
import sys
import uuid
from datetime import datetime, timezone
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
from app.models.capture import ExtractionResult, ExtractedTask, ExtractedSubTask  # noqa: E402


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table, store):
        self.table_name, self.store = table, store
        self.filters, self.op, self.payload = [], "select", None
        self.want_single = False

    def select(self, *_):
        self.op = "select"; return self
    def insert(self, row):
        self.op = "insert"; self.payload = row; return self
    def eq(self, f, v):
        self.filters.append((f, v)); return self
    def maybe_single(self):
        self.want_single = True; return self

    def _matches(self, row):
        return all(str(row.get(f)) == str(v) for f, v in self.filters)

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

# What we pretend Gemini returned — a realistic hierarchy, matching the
# shape used throughout the wireframes (AtlasDev / capability PDF example).
FAKE_EXTRACTION = ExtractionResult(
    transcript="Okay so I need to follow up with Fred on the AtlasDev brand assets...",
    hierarchy=[
        ExtractedTask(
            label="Follow up on AtlasDev brand assets",
            base_urgency=3,
            due_at="2026-08-01T17:00:00Z",
            sub_tasks=[
                ExtractedSubTask(label="Export dark-background logo variants", base_urgency=2, due_at=None),
                ExtractedSubTask(label="Update capability PDF", base_urgency=3, due_at=None),
            ],
        ),
        ExtractedTask(label="Fix Trading Bot drawdown logic", base_urgency=4, due_at=None, sub_tasks=[]),
    ],
)

print("=" * 60)
print("TEST 1: POST /v1/capture/voice — wrong content type -> 400")
r = client.post(
    "/v1/capture/voice",
    files={"audio": ("note.txt", io.BytesIO(b"not audio"), "text/plain")},
)
print(f"  Status: {r.status_code} (expect 400)")
assert r.status_code == 400

print("\nTEST 2: POST /v1/capture/voice — empty audio file -> 400")
r = client.post(
    "/v1/capture/voice",
    files={"audio": ("note.m4a", io.BytesIO(b""), "audio/m4a")},
)
print(f"  Status: {r.status_code} (expect 400)")
assert r.status_code == 400

print("\nTEST 3: POST /v1/capture/voice — happy path (Gemini call faked)")
with patch("app.routers.capture.extract_hierarchy_from_audio", return_value=FAKE_EXTRACTION):
    r = client.post(
        "/v1/capture/voice",
        files={"audio": ("note.m4a", io.BytesIO(b"fake audio bytes"), "audio/m4a")},
    )
assert r.status_code == 201, r.text
voice_response = r.json()
voice_log_id = voice_response["voice_log_id"]
print(f"  Status: 201, voice_log_id: {voice_log_id}")
print(f"  Hierarchy items returned: {len(voice_response['hierarchy'])} (expect 2)")
assert len(voice_response["hierarchy"]) == 2
print("  Nothing saved to tasks table yet (confirm hasn't been called):",
      len(fake_db.store.get("tasks", [])) == 0)
assert len(fake_db.store.get("tasks", [])) == 0

print("\nTEST 4: POST /v1/capture/voice — Gemini failure -> 502, not 500")
with patch("app.routers.capture.extract_hierarchy_from_audio", side_effect=RuntimeError("model overloaded")):
    r = client.post(
        "/v1/capture/voice",
        files={"audio": ("note.m4a", io.BytesIO(b"fake audio bytes"), "audio/m4a")},
    )
print(f"  Status: {r.status_code} (expect 502)")
assert r.status_code == 502

print("\nTEST 5: POST /v1/capture/confirm — save the hierarchy as real tasks")
confirm_body = {
    "voice_log_id": voice_log_id,
    "hierarchy": [
        {
            "label": "Follow up on AtlasDev brand assets",
            "base_urgency": 3,
            "due_at": "2026-08-01T17:00:00Z",
            "sub_tasks": [
                {"label": "Export dark-background logo variants", "base_urgency": 2, "sub_tasks": []},
                {"label": "Update capability PDF", "base_urgency": 3, "sub_tasks": []},
            ],
        },
        {"label": "Fix Trading Bot drawdown logic", "base_urgency": 4, "sub_tasks": []},
    ],
}
r = client.post("/v1/capture/confirm", json=confirm_body)
assert r.status_code == 201, r.text
created = r.json()
print(f"  Tasks created: {len(created)} (expect 4 — 2 parents + 2 sub-tasks)")
assert len(created) == 4

parent_with_due = next(t for t in created if t["label"] == "Follow up on AtlasDev brand assets")
print(f"  Parent's source is 'voice': {parent_with_due['source'] == 'voice'}")
assert parent_with_due["source"] == "voice"

sub_tasks = [t for t in created if t["parent_task_id"] == parent_with_due["id"]]
print(f"  Sub-tasks correctly linked to parent: {len(sub_tasks)} (expect 2)")
assert len(sub_tasks) == 2

print("\nTEST 6: Reminders auto-created for the task with due_at")
reminders_for_parent = [r for r in fake_db.store.get("task_reminder_preferences", []) if r["task_id"] == parent_with_due["id"]]
print(f"  Reminders created: {len(reminders_for_parent)} (expect 2 — 60min and 30min before)")
assert len(reminders_for_parent) == 2

print("\nTEST 7: voice_log_tasks join rows created, linking all 4 tasks back to the voice log")
joins = [j for j in fake_db.store.get("voice_log_tasks", []) if j["voice_log_id"] == voice_log_id]
print(f"  Join rows created: {len(joins)} (expect 4)")
assert len(joins) == 4

print("\n" + "=" * 60)
print("ALL 7 TESTS PASSED")

from datetime import datetime, timezone

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.models.capture import ExtractionResult

EXTRACTION_PROMPT = """\
You are extracting a task list from a voice memo. The speaker is thinking
out loud about things they need to do.

Return:
1. "transcript" — a faithful transcription of exactly what was said.
2. "hierarchy" — a list of tasks mentioned. Group related sub-steps under
   a parent task using "sub_tasks"; only nest one level deep (sub_tasks
   never have their own sub_tasks). Standalone items with no natural
   grouping are their own top-level task with an empty sub_tasks list.

For each task and sub-task, infer:
- "base_urgency" (1-5): how urgent it sounds from tone and content.
  1 = someday/maybe, 3 = normal, 5 = urgent/time-critical. Default to 2
  if genuinely unclear.
- "due_at": an ISO8601 timestamp ONLY if a specific date/time is stated
  or clearly implied (e.g. "by Friday", "tomorrow morning"). Use null if
  no timing is mentioned — do not guess a date that wasn't said.

Current date/time for resolving relative dates like "tomorrow" or
"Friday": {current_time}
"""


def get_gemini_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is blank — set it in .env before using any /v1/capture endpoint."
        )
    return genai.Client(api_key=settings.gemini_api_key)


def extract_hierarchy_from_audio(audio_bytes: bytes, mime_type: str) -> ExtractionResult:
    """
    Sends the audio directly to Gemini (inline, not via the Files API —
    voice notes here are short, per the PRD's under-60-second acceptance
    criteria, so there's no need for the extra upload round-trip that's
    worth it for larger files).

    Returns a validated ExtractionResult. Raises if Gemini's response
    doesn't parse — the caller (the /capture/voice route) is responsible
    for turning that into a proper HTTP error.
    """
    client = get_gemini_client()

    prompt = EXTRACTION_PROMPT.format(current_time=datetime.now(timezone.utc).isoformat())
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt, audio_part],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionResult,
        ),
    )

    # response.parsed would hand us an ExtractionResult directly, but we
    # re-validate from response.text ourselves instead — belt and braces.
    # Schema adherence is strong, not guaranteed; this is still input from
    # outside our system, same as any request body.
    return ExtractionResult.model_validate_json(response.text)

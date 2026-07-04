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

EXTRACTION_PROMPT_IMAGE = """\
You are extracting a task list from a photo — this could be a whiteboard,
a handwritten note, a screenshot, or a printed document. The image may
contain messy handwriting, partial words, or non-task content mixed in
(dates, doodles, unrelated notes) — extract only the actionable items.

Return:
1. "transcript" — a faithful transcription of all readable text in the
   image, in reading order. If handwriting is ambiguous, transcribe your
   best interpretation rather than omitting it.
2. "hierarchy" — a list of tasks found in the text. Group related
   sub-steps under a parent task using "sub_tasks"; only nest one level
   deep (sub_tasks never have their own sub_tasks). Standalone items with
   no natural grouping are their own top-level task with an empty
   sub_tasks list. If nothing in the image reads as an actionable task,
   return an empty hierarchy list — do not invent tasks that aren't there.

For each task and sub-task, infer:
- "base_urgency" (1-5): how urgent it appears from context (underlines,
  exclamation marks, "URGENT" labels, etc. all count as signals).
  1 = someday/maybe, 3 = normal, 5 = urgent/time-critical. Default to 2
  if genuinely unclear.
- "due_at": an ISO8601 timestamp ONLY if a specific date/time is written
  or clearly implied. Use null if no timing is present — do not guess a
  date that isn't actually in the image.

Current date/time for resolving relative dates like "tomorrow" or
"Friday": {current_time}
"""


STATUS_UPDATE_PROMPT = """\
Write a short status update summarizing the following completed and
in-progress tasks, for an audience called "{audience_name}".

{tone_instruction}

Tasks to summarize:
{task_list}

Write it as a short, natural paragraph (or a couple of short paragraphs
for a longer list) — not a bulleted list, not a formal report. This is a
draft the person will review and edit before sending, so it should read
like something a person would actually write, not a corporate summary.
Do not invent details that aren't in the task list above.
"""


def generate_status_update_draft(
    task_labels: list[str],
    audience_name: str,
    tone_notes: str | None,
) -> str:
    """
    Plain text in, plain text out — simpler than the voice/image
    extraction functions above, since there's no audio/image input and
    no structured schema to enforce. Just one string.
    """
    client = get_gemini_client()

    tone_instruction = (
        f"Tone for this audience: {tone_notes}"
        if tone_notes
        else "No specific tone guidance given — write in a clear, friendly, professional default tone."
    )
    task_list = "\n".join(f"- {label}" for label in task_labels)

    prompt = STATUS_UPDATE_PROMPT.format(
        audience_name=audience_name,
        tone_instruction=tone_instruction,
        task_list=task_list,
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt],
    )
    return response.text.strip()


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


def extract_hierarchy_from_image(image_bytes: bytes, mime_type: str) -> ExtractionResult:
    """
    Same pattern as extract_hierarchy_from_audio, OCR-flavored prompt
    instead. Reuses the same ExtractionResult schema — the field is still
    called .transcript here even though it holds OCR'd text for this
    path; deliberately not adding a second near-identical Pydantic model
    just for a field name. The API layer (capture.py) is what actually
    exposes it as "ocr_text" in the real HTTP response, matching what the
    contract promises.
    """
    client = get_gemini_client()

    prompt = EXTRACTION_PROMPT_IMAGE.format(current_time=datetime.now(timezone.utc).isoformat())
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionResult,
        ),
    )

    return ExtractionResult.model_validate_json(response.text)

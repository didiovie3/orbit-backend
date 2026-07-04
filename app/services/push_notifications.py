import os
from functools import lru_cache

from app.core.config import get_settings


@lru_cache
def _get_firebase_app():
    """
    Lazily initializes the Firebase Admin SDK, once per process. Tries
    two credential paths, in order:

    1. A downloaded service account JSON key (FCM_SERVICE_ACCOUNT_PATH) —
       the "normal" path, but many Google Cloud projects now block key
       creation by an organization policy (iam.disableServiceAccountKeyCreation),
       a security default that's become increasingly common.
    2. Application Default Credentials — your own `gcloud auth
       application-default login` session. Sidesteps the key-creation
       policy entirely since nothing gets downloaded; the SDK finds these
       credentials automatically on your machine. This is the path to
       use if option 1 hits that policy wall.

    Returns None (not an error) if neither is available — same "not
    built yet, degrade gracefully" pattern as Sentry's blank DSN and
    Drive's null drive_file_id elsewhere in this backend. Still genuinely
    optional right now regardless: the Android app doesn't collect or
    store a device token yet (Step 6), so there's nowhere to deliver a
    push to even with this fully configured.
    """
    import firebase_admin
    from firebase_admin import credentials

    settings = get_settings()

    if os.path.exists(settings.fcm_service_account_path):
        cred = credentials.Certificate(settings.fcm_service_account_path)
        return firebase_admin.initialize_app(cred)

    try:
        cred = credentials.ApplicationDefault()
        return firebase_admin.initialize_app(cred, {"projectId": settings.fcm_project_id})
    except Exception:
        # Neither a key file nor `gcloud auth application-default login`
        # has been set up yet — not an error, just not configured.
        return None


def send_push_notification(fcm_token: str | None, title: str, body: str) -> bool:
    """
    Returns True if a push was actually sent, False if it was skipped
    (no app configured, or no token for this user — e.g. they haven't
    opened the Android app yet, or notifications aren't set up). Never
    raises — a failed/skipped push should never take down the job that
    called it; the reminder still "fires" and gets logged either way,
    the notification is just one (currently unbuilt) delivery channel
    for it.
    """
    if not fcm_token:
        return False

    app = _get_firebase_app()
    if app is None:
        return False

    from firebase_admin import messaging

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        token=fcm_token,
    )
    try:
        messaging.send(message, app=app)
        return True
    except Exception:
        # A bad/expired token, network issue, etc. — logged by whatever
        # calls this (Sentry will pick it up once this is wrapped in a
        # real job runner), but doesn't propagate. One failed push
        # shouldn't fail the whole reminder-firing job for every task.
        return False

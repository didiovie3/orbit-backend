import sys
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.auth import CurrentUser, get_current_user
from app.core.config import check_required_settings, get_settings
from app.core.scheduler import start_scheduler, stop_scheduler
from app.routers import (
    audiences,
    auth,
    briefs,
    calendar,
    capture,
    drive,
    health,
    notes,
    projects,
    reminder_events,
    status_updates,
    tasks,
    time_blocks,
    users,
)

missing = check_required_settings()
if missing:
    print(
        f"\n✗ Missing required .env values: {', '.join(missing)}\n"
        f"  Copy .env.example to .env and fill these in before starting the server.\n",
        file=sys.stderr,
    )
    sys.exit(1)

settings = get_settings()

# Only turns on if SENTRY_DSN is actually set — blank locally is fine and
# just means nothing gets sent anywhere. sentry_sdk.init() has to run
# before the FastAPI app is created so it can hook into everything from
# the start, including things that happen during startup itself.
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        # Fraction of requests to trace for performance monitoring, not
        # just errors. 1.0 = 100% is fine at this scale; dial down once
        # there's real traffic, since it costs Sentry-side quota.
        traces_sample_rate=1.0,
    )
    print(f"✓ Sentry initialized (environment: {settings.env})")
else:
    print("○ Sentry not configured — SENTRY_DSN is blank, errors won't be reported")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the server starts, before it accepts any requests.
    start_scheduler()
    yield
    # Runs once when the server shuts down (Ctrl+C) — lets any in-flight
    # job finish cleanly instead of getting killed mid-update.
    stop_scheduler()


app = FastAPI(title="Orbit API", version="0.1.0", lifespan=lifespan)

# Dev-permissive CORS. Tighten this before shipping — the Android app talks
# over Retrofit (not a browser), so CORS mainly matters if/when you ever
# hit these endpoints from a web tool or Postman with credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every route in the app lives under /v1, per the API contract doc.
# As you build out Step 5, each new router (tasks, projects, notes, ...)
# gets its own file in app/routers/ and gets included here the same way.
app.include_router(health.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(tasks.router, prefix="/v1")
app.include_router(projects.router, prefix="/v1")
app.include_router(notes.router, prefix="/v1")
app.include_router(audiences.router, prefix="/v1")
app.include_router(capture.router, prefix="/v1")
app.include_router(status_updates.router, prefix="/v1")
app.include_router(calendar.router, prefix="/v1")
app.include_router(users.router, prefix="/v1")
app.include_router(briefs.router, prefix="/v1")
app.include_router(drive.router, prefix="/v1")
app.include_router(time_blocks.router, prefix="/v1")
app.include_router(reminder_events.router, prefix="/v1")


@app.get("/")
def root():
    return {"service": "orbit-api", "status": "running"}


# Debug/dev-only tools — never part of the API contract. Two layers, not
# one: they only get registered at all when env is "dev" (so there's no
# route to even guess at once this runs somewhere real), AND they require
# a logged-in user like every real endpoint, rather than being reachable
# by anyone who finds the URL.
if settings.env == "dev":

    @app.get("/debug-sentry")
    def debug_sentry(current_user: CurrentUser = Depends(get_current_user)):
        """
        Deliberately broken on purpose — hit this once after setting a real
        SENTRY_DSN to confirm errors actually show up in your Sentry
        dashboard.
        """
        return 1 / 0

    @app.post("/debug/run-escalation")
    def debug_run_escalation(current_user: CurrentUser = Depends(get_current_user)):
        """Manually triggers the escalation job instead of waiting up to an
        hour for it to fire on its own schedule."""
        from app.db.supabase_client import get_supabase
        from app.services.escalation import run_escalation_job

        return run_escalation_job(get_supabase())

    @app.post("/debug/run-overdue-flip")
    def debug_run_overdue_flip(current_user: CurrentUser = Depends(get_current_user)):
        """Manually triggers the overdue-flip job."""
        from app.db.supabase_client import get_supabase
        from app.services.escalation import run_overdue_flip_job

        return run_overdue_flip_job(get_supabase())

    @app.post("/debug/fire-reminders")
    def debug_fire_reminders(current_user: CurrentUser = Depends(get_current_user)):
        """Manually triggers the reminder-firing job instead of waiting up to
        a minute for it to fire on its own schedule."""
        from app.db.supabase_client import get_supabase
        from app.services.reminders import run_fire_reminders_job

        return run_fire_reminders_job(get_supabase())

    @app.post("/debug/renew-webhooks")
    def debug_renew_webhooks(current_user: CurrentUser = Depends(get_current_user)):
        """Manually triggers the calendar webhook renewal job instead of
        waiting up to 6 hours for it to fire on its own schedule."""
        from app.db.supabase_client import get_supabase
        from app.services.calendar_sync import run_renew_webhooks_job

        return run_renew_webhooks_job(get_supabase())

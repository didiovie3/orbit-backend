import sys

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import check_required_settings, get_settings
from app.routers import health

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

app = FastAPI(title="Orbit API", version="0.1.0")

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


@app.get("/")
def root():
    return {"service": "orbit-api", "status": "running"}


@app.get("/debug-sentry")
def debug_sentry():
    """
    Deliberately broken on purpose — hit this once after setting a real
    SENTRY_DSN to confirm errors actually show up in your Sentry
    dashboard. Delete this route once you've verified it, or leave it —
    either is fine, but it shouldn't ship to a real production build.
    """
    return 1 / 0

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import check_required_settings
from app.routers import health

missing = check_required_settings()
if missing:
    print(
        f"\n✗ Missing required .env values: {', '.join(missing)}\n"
        f"  Copy .env.example to .env and fill these in before starting the server.\n",
        file=sys.stderr,
    )
    sys.exit(1)

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

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.supabase_client import get_supabase
from app.services.escalation import run_escalation_job, run_overdue_flip_job
from app.services.reminders import run_fire_reminders_job

scheduler = AsyncIOScheduler()


def _run_escalation():
    result = run_escalation_job(get_supabase())
    print(f"[scheduler] Escalation job: {result}")


def _run_overdue_flip():
    result = run_overdue_flip_job(get_supabase())
    print(f"[scheduler] Overdue flip job: {result}")


def _run_fire_reminders():
    result = run_fire_reminders_job(get_supabase())
    print(f"[scheduler] Reminder firing job: {result}")


def start_scheduler():
    # Escalation and the overdue flip match the PRD's "runs every hour"
    # spec exactly. Reminder firing runs every minute — reminders are
    # time-sensitive to the minute in a way urgency escalation isn't;
    # nobody notices if their task's urgency updates a few minutes late,
    # but a reminder firing 10 minutes after it was supposed to defeats
    # the point.
    scheduler.add_job(_run_escalation, "interval", hours=1, id="escalation")
    scheduler.add_job(_run_overdue_flip, "interval", hours=1, id="overdue_flip")
    scheduler.add_job(_run_fire_reminders, "interval", minutes=1, id="fire_reminders")
    scheduler.start()
    print("✓ Scheduler started — escalation & overdue flip hourly, reminders every minute")


def stop_scheduler():
    scheduler.shutdown()

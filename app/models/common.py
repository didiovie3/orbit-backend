from pydantic import BaseModel


class DeleteConfirm(BaseModel):
    """
    Shared by every hard-delete endpoint in the contract (tasks, projects,
    notes, audiences) — all of them require {"confirmed": true} in the
    body specifically so the Android app is forced to show a confirmation
    dialog before this can ever be called, matching PRD §5.12.

    Deliberately has no validator enforcing confirmed=True here — Pydantic
    validation failures return 422 by default, but the contract promises
    400 specifically for this case. The actual check happens in each
    delete endpoint instead, so we control the exact response.
    """

    confirmed: bool = False

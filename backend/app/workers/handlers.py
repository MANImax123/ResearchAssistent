"""Job handlers: async functions doing the actual work, keyed by job type.

A handler receives a JobReporter plus the job's params, and returns a JSON-able
result (stored in `jobs.result_ref`) or None.
"""

import asyncio
import uuid
from typing import Any

from app.db.models.enums import StageName, StageStatus
from app.services.jobs import JobHandler, JobReporter


async def demo(
    reporter: JobReporter, steps: int = 5, delay_s: float = 1.0, fail_at: int | None = None
) -> dict[str, Any]:
    """Sleeps in steps, reporting progress. `fail_at` makes it raise at that step (for testing failures)."""
    for step in range(1, steps + 1):
        await asyncio.sleep(delay_s)
        if fail_at is not None and step == fail_at:
            raise RuntimeError(f"Demo failure at step {step}")
        await reporter.progress(step / steps * 100, f"Step {step} of {steps}")
    return {"steps": steps, "message": "Demo job completed"}


async def stage_run(
    reporter: JobReporter,
    project_id: str,
    stage: str,
    previous_status: str,
    mode: str = "run",
    feedback: str | None = None,
    by: str | None = None,
) -> dict[str, Any]:
    """Run a stage's agent through the orchestrator; succeeds once the stage is paused for review.

    mode "run": fresh run or re-run (optional feedback); mode "regenerate": the stage is paused for
    review and its agent runs again with the feedback.
    """
    from app.orchestrator import service as orchestrator  # imported lazily: keeps LangGraph out of worker boot
    from app.orchestrator.graph import STAGE_LABELS

    stage_name = StageName(stage)
    pid = uuid.UUID(project_id)
    verb = "Regenerating" if mode == "regenerate" or feedback else "Running"
    await reporter.progress(5, f"{verb} {STAGE_LABELS[stage_name]}")
    try:
        if mode == "regenerate":
            decision = {"action": "regenerate", "comment": feedback, "by": by}
            snapshot = await orchestrator.review(pid, decision, session_factory=reporter.session_factory)
        else:
            snapshot = await orchestrator.run_stage(
                pid, stage_name, session_factory=reporter.session_factory, feedback=feedback
            )
    except Exception:
        # Put the stage back the way it was so it can be run again
        await orchestrator.set_stage_status(pid, stage_name, StageStatus(previous_status), reporter.session_factory)
        raise
    return {
        "stage": stage,
        "status": snapshot.values.get("stage_status", {}).get(stage),
        "checkpoint_id": snapshot.checkpoint_id,
    }


HANDLERS: dict[str, JobHandler] = {
    "demo": demo,
    "stage_run": stage_run,
}

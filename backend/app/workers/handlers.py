"""Job handlers: async functions doing the actual work, keyed by job type.

A handler receives a JobReporter plus the job's params, and returns a JSON-able
result (stored in `jobs.result_ref`) or None.
"""

import asyncio
from typing import Any

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


HANDLERS: dict[str, JobHandler] = {
    "demo": demo,
}

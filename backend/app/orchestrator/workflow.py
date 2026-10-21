"""Stage prerequisites and the staleness rule (B6, B12).

    idea → literature → methodology → results ─┬─▶ introduction ─┬─▶ references → review → formatting
                                                └─▶ conclusion ───┘

A stage may run only when all its prerequisites are approved. When an upstream stage changes
(edited, reopened, regenerated), every stage that depends on it — directly or transitively — and
has already been worked on becomes `stale`.
"""

from collections.abc import Mapping

from app.db.models.enums import StageName as S
from app.db.models.enums import StageStatus

PREREQUISITES: dict[S, tuple[S, ...]] = {
    S.IDEA: (),
    S.LITERATURE: (S.IDEA,),
    S.METHODOLOGY: (S.LITERATURE,),
    S.RESULTS: (S.METHODOLOGY,),
    # Introduction and final abstract are written after results, from the full approved context
    S.INTRODUCTION: (S.RESULTS,),
    S.CONCLUSION: (S.RESULTS,),
    S.REFERENCES: (S.INTRODUCTION, S.CONCLUSION),
    S.REVIEW: (S.REFERENCES,),
    S.FORMATTING: (S.REVIEW,),
}

# Statuses from which a stage can be (re)run
RUNNABLE = frozenset({StageStatus.NOT_STARTED, StageStatus.REJECTED, StageStatus.STALE})


def dependents(stage: S) -> list[S]:
    """Every stage that directly or transitively depends on `stage`, in pipeline order."""
    found: set[S] = set()
    frontier = [stage]
    while frontier:
        current = frontier.pop()
        for candidate, prereqs in PREREQUISITES.items():
            if current in prereqs and candidate not in found:
                found.add(candidate)
                frontier.append(candidate)
    return [s for s in S if s in found]


def blocked_by(stage: S, statuses: Mapping[S, StageStatus]) -> list[S]:
    """Prerequisites of `stage` that are not approved yet."""
    return [p for p in PREREQUISITES[stage] if statuses.get(p) != StageStatus.APPROVED]


def stale_after_change(stage: S, statuses: Mapping[S, StageStatus]) -> list[S]:
    """Dependents that must be marked stale when `stage` changes (ones never started stay as they are)."""
    return [d for d in dependents(stage) if statuses.get(d, StageStatus.NOT_STARTED) != StageStatus.NOT_STARTED]

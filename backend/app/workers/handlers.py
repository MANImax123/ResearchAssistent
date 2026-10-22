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


async def idea_critique(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """Novelty, Feasibility and Methodology critics review the research plan in parallel."""
    from app.services import idea_review

    await reporter.progress(10, "Critics are reviewing the plan")
    critiques = await idea_review.critique_plan(uuid.UUID(project_id), reporter.session_factory)
    return {"round": critiques.round, "scores": {name: c.score for name, c in critiques.critiques.items()}}


async def idea_revise(
    reporter: JobReporter, project_id: str, suggestion_ids: list[str], by: str | None = None
) -> dict[str, Any]:
    """Revise the research plan applying only the selected critic suggestions."""
    from app.services import idea_review

    await reporter.progress(10, f"Applying {len(suggestion_ids)} suggestion(s)")
    await idea_review.revise_plan(uuid.UUID(project_id), suggestion_ids, reporter.session_factory, by=by)
    return {"applied": suggestion_ids}


async def idea_abstract(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """Draft the working abstract from the research plan (length-controlled)."""
    from app.services import idea_review

    await reporter.progress(10, "Drafting the abstract")
    result = await idea_review.draft_abstract(uuid.UUID(project_id), reporter.session_factory)
    return {"words": result.word_count, "within_limit": result.within_limit, "attempts": result.attempts}


async def literature_plan(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """L1: search queries from the approved research plan."""
    from app.services import literature

    await reporter.progress(10, "Planning search queries")
    queries = await literature.generate_queries(uuid.UUID(project_id), reporter.session_factory)
    return {"queries": len(queries)}


async def literature_discover(
    reporter: JobReporter,
    project_id: str,
    sources: list[str],
    per_source: int,
    max_new: int,
    query_ids: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    venue: str | None = None,
) -> dict[str, Any]:
    """L2: run the queries on the academic sources and add the deduplicated papers to the library."""
    from app.agents.literature.discovery import Filters
    from app.services import literature
    from app.services.academic.sources import SOURCE_LABEL

    await reporter.progress(5, "Searching")
    result = await literature.run_discovery(
        uuid.UUID(project_id),
        reporter.session_factory,
        query_ids=[uuid.UUID(q) for q in query_ids] if query_ids else None,
        sources=sources,  # type: ignore[arg-type]
        filters=Filters(year_from=year_from, year_to=year_to, venue=venue),
        per_source=per_source,
        max_new=max_new,
        progress=reporter.progress,
    )
    return literature.summary(result, lambda s: SOURCE_LABEL.get(s, s))


async def literature_rank(reporter: JobReporter, project_id: str, top_n: int) -> dict[str, Any]:
    """L3: embed papers, score them against the research questions, and have the LLM judge the top ones."""
    from app.services import literature

    await reporter.progress(5, "Ranking papers")
    return await literature.rank_papers(
        uuid.UUID(project_id), reporter.session_factory, top_n=top_n, progress=reporter.progress
    )


async def literature_parse(reporter: JobReporter, project_id: str, paper_ids: list[str]) -> dict[str, Any]:
    """L4: fetch open-access PDFs, parse them (GROBID, PyMuPDF fallback) and chunk the text."""
    from app.services import literature

    await reporter.progress(5, f"Getting full text for {len(paper_ids)} papers")
    return await literature.run_parsing(
        uuid.UUID(project_id),
        reporter.session_factory,
        [uuid.UUID(p) for p in paper_ids],
        progress=reporter.progress,
    )


async def literature_index(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """L5: embed parsed passages that aren't searchable yet."""
    from app.services import literature

    return await literature.index_chunks(uuid.UUID(project_id), reporter.session_factory, progress=reporter.progress)


async def literature_analyze(reporter: JobReporter, project_id: str, paper_ids: list[str]) -> dict[str, Any]:
    """L6: a structured, evidence-backed profile for each paper."""
    from app.services import literature

    await reporter.progress(5, f"Analysing {len(paper_ids)} papers")
    return await literature.run_analysis(
        uuid.UUID(project_id), reporter.session_factory, [uuid.UUID(p) for p in paper_ids], progress=reporter.progress
    )


async def literature_compare(
    reporter: JobReporter, project_id: str, force: bool = False, column: str | None = None
) -> dict[str, Any]:
    """L7: build the comparison matrix (built-in columns), or fill one custom column."""
    from app.agents import registry
    from app.services import comparison

    pid = uuid.UUID(project_id)
    llm = registry.llm_factory(reporter.session_factory)()
    await reporter.progress(5, "Comparing papers")
    if column:
        return await comparison.fill_column(
            pid, column, reporter.session_factory, llm, registry.embeddings_factory(), progress=reporter.progress
        )
    return await comparison.build(pid, reporter.session_factory, llm, force=force, progress=reporter.progress)


async def literature_contradictions(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """L8: conflicting claims between selected papers that share a dataset or metric."""
    from app.agents import registry
    from app.services import contradictions

    await reporter.progress(5, "Grouping papers by dataset and metric")
    return await contradictions.detect(
        uuid.UUID(project_id),
        reporter.session_factory,
        registry.llm_factory(reporter.session_factory)(),
        registry.embeddings_factory(),
        progress=reporter.progress,
    )


async def knowledge_graph(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """L9: rebuild the project's Neo4j graph from its papers, profiles, contradictions and plan."""
    from app.agents import registry
    from app.services import knowledge_graph as kg

    await reporter.progress(10, "Building the knowledge graph")
    client = registry.graph_factory()
    await client.ensure_schema()
    return await kg.build(uuid.UUID(project_id), reporter.session_factory, client)


async def research_gaps(reporter: JobReporter, project_id: str) -> dict[str, Any]:
    """L10: potential gaps from limitations, contradictions, untested method × dataset pairs and future work."""
    from app.agents import registry
    from app.services import gaps

    client = registry.graph_factory()
    await client.ensure_schema()
    return await gaps.detect(
        uuid.UUID(project_id),
        reporter.session_factory,
        registry.llm_factory(reporter.session_factory)(),
        client,
        progress=reporter.progress,
    )


HANDLERS: dict[str, JobHandler] = {
    "demo": demo,
    "stage_run": stage_run,
    "idea_critique": idea_critique,
    "idea_revise": idea_revise,
    "idea_abstract": idea_abstract,
    "literature_plan": literature_plan,
    "literature_discover": literature_discover,
    "literature_rank": literature_rank,
    "literature_parse": literature_parse,
    "literature_index": literature_index,
    "literature_analyze": literature_analyze,
    "literature_compare": literature_compare,
    "literature_contradictions": literature_contradictions,
    "knowledge_graph": knowledge_graph,
    "research_gaps": research_gaps,
}

"""Step 4.10: plan refinements from accepted gaps, and the Literature Survey section (the Literature stage).

Acceptance: the section is thematic, every citation key resolves to a verified paper of the project,
accepted refinements edit the approved plan (staleness rule), and stage 2 can be approved.
"""

import json
import re
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.idea.schemas import IdeaPlan
from app.agents.literature import refine
from app.agents.literature import survey as writer
from app.core.config import get_settings
from app.db.models import ManuscriptTable, Paper, PaperProfile, ResearchGap
from app.db.models.enums import GapConfidence, GapStatus, JobStatus, PaperOrigin, ParseStatus, SectionType, StageName
from app.services import survey
from app.services.jobs import execute_job
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.workers.handlers import HANDLERS
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})


# ─── Units ──────────────────────────────────────────────────────────────


def test_citations_become_keys_and_unknown_ids_are_dropped() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    text, cited, dropped = writer.to_keys(
        "GNNs win [P1, P2]. Others disagree [P2; P9]. Also [P1][P2] .", {"P1": a, "P2": b}
    )
    assert text == f"GNNs win [@{a}][@{b}]. Others disagree [@{b}]. Also [@{a}][@{b}]."
    assert cited == [a, b, b, a, b] and dropped == 1


def test_every_paragraph_must_cite_and_gaps_must_be_hedged() -> None:
    with pytest.raises(ValidationError, match="paragraph 2 cites no paper"):
        writer.ThemeText(paragraphs=["Graph models dominate recent work on affinity [P1].",
                                     "Sequence models are an older line of work without structure."])  # fmt: skip
    with pytest.raises(ValidationError, match="states gaps as facts"):
        writer.Frame(opening="This section reviews affinity prediction work [P1] in three themes.",
                     gap_summary=["No study has evaluated GNNs on cold targets, which proves a gap [P1]."])  # fmt: skip
    writer.Frame(opening="This section reviews affinity prediction work [P1] in three themes.",
                 gap_summary=["Cold-target evaluation appears rare among the reviewed studies [P1]."])  # fmt: skip


def test_outline_covers_every_paper() -> None:
    labels = {f"P{i}": uuid.uuid4() for i in range(1, 5)}
    outline = writer.Outline(themes=[writer.Theme(title="Graph models", papers=["P1", "p2", "P7"]),
                                     writer.Theme(title="Baselines", papers=["P2", "P3"])])  # fmt: skip
    themes = writer._fix_outline(outline, labels)
    assert themes[0].papers[:2] == ["P1", "P2"] and "P7" not in themes[0].papers  # case fixed, unknown dropped
    assert {p for t in themes for p in t.papers} == set(labels)  # P4 joined a theme


PLAN_OBJ = IdeaPlan.model_validate(PLAN)


def test_refinement_changes_apply_to_the_plan() -> None:
    plan, created = refine.apply(PLAN_OBJ, {"kind": "modify_objective", "target": "O2",
                                            "text": "Evaluate ranking quality on cold-target splits."})  # fmt: skip
    assert plan.objectives[1].text.endswith("cold-target splits.") and created == []
    plan, created = refine.apply(PLAN_OBJ, {"kind": "add_question", "text": "Do GNN gains hold on unseen targets?",
                                            "question_kind": "question", "linked_objective_ids": ["O2"]})  # fmt: skip
    assert created == ["RQ3"] and plan.research_questions[-1].linked_objective_ids == ["O2"]
    plan, created = refine.apply(PLAN_OBJ, {"kind": "add_objective", "text": "Assess robustness to data imbalance.",
                                            "question_text": "Does imbalance affect stability?"})  # fmt: skip
    assert created == ["O4", "RQ3"] and plan.research_questions[-1].linked_objective_ids == ["O4"]
    with pytest.raises(refine.InvalidChangeError, match="phrased as a question"):
        refine.apply(PLAN_OBJ, {"kind": "add_question", "text": "Do errors cluster by family?",
                                "question_kind": "hypothesis", "linked_objective_ids": ["O3"]})  # fmt: skip
    with pytest.raises(refine.InvalidChangeError, match="RQ9 doesn't exist"):
        refine.apply(PLAN_OBJ, {"kind": "modify_question", "target": "RQ9", "text": "Something else entirely?"})
    with pytest.raises(refine.InvalidChangeError, match="O9"):
        refine.apply(PLAN_OBJ, {"kind": "add_question", "text": "A question linked to nothing real?",
                                "linked_objective_ids": ["O9"]})  # fmt: skip


# ─── API flow ───────────────────────────────────────────────────────────


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    idea = FakeLLM(responses=[PLAN])

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    def agents(sf: Any) -> dict[StageName, Any]:
        async def inputs(pid: uuid.UUID, selected: list[str], gap_ids: list[str]) -> writer.SurveyInputs:
            return await survey.load_inputs(sf, pid, selected, gap_ids)

        return {
            StageName.IDEA: make_idea_agent(lambda: LLMService(idea, SETTINGS, sf), no_notes),
            StageName.LITERATURE: writer.make_literature_agent(lambda: LLMService(fake, SETTINGS, sf), inputs),
        }

    monkeypatch.setattr(registry, "agent_factory", agents)
    monkeypatch.setattr(registry, "llm_factory", lambda sf: lambda: LLMService(fake, SETTINGS, sf))
    return fake


async def add_paper(flow: Flow, db: AsyncSession, title: str, *, doi: str | None, profiled: bool = True) -> Paper:
    paper = Paper(project_id=flow.pid, title=title, year=2022, origin=PaperOrigin.SEARCH, sources=[],
                  authors=[{"name": "A. Author"}, {"name": "B. Author"}], selected=True, doi=doi,
                  parse_status=ParseStatus.PARSED, abstract=f"{title} abstract.")  # fmt: skip
    db.add(paper)
    await db.flush()
    if profiled:
        db.add(PaperProfile(paper_id=paper.id, problem="Affinity prediction.", method=f"{title.split(':')[0]} model.",
                            results="RMSE 1.3 on PDBbind.", dataset=["PDBbind"], metrics=["RMSE"],
                            limitations=["No cold-target split."], tech_stack=[], evidence={}))  # fmt: skip
    await db.commit()
    return paper


def labels_in(prompt: str) -> dict[str, str]:
    """P# → title, as listed in a writer prompt."""
    return {m.group(1): m.group(2) for m in re.finditer(r"^(P\d+) — (.+?)(?: \(\d{4}\))?$", prompt, re.M)}


async def survey_read(flow: Flow) -> dict[str, Any]:
    resp = await flow.client.get(flow.url("/literature/survey"), headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def job_ok(flow: Flow, path: str) -> dict[str, Any]:
    resp = await flow.post(path)
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


def survey_script(fake: FakeLLM, *, bad_first_theme: bool = False) -> None:
    def outline(prompt: str) -> str:
        assert "Unverified preprint" not in prompt  # papers without a verified id are never offered for citing
        assert set(labels_in(prompt)) == {"P1", "P2", "P3"}
        return json.dumps({"themes": [
            {"title": "Graph neural networks for affinity", "focus": "structure-aware models", "papers": ["P1", "P2"]},
            {"title": "Evaluation practice", "focus": "benchmarks and splits", "papers": ["P2", "P3"]},
        ]})  # fmt: skip

    good_graph = {"paragraphs": [
        "Graph-based models now dominate affinity prediction [P1, P2], learning from molecular structure "
        "rather than fingerprints [P1].",
        "Both report RMSE around 1.3 on PDBbind [P1][P2], although their architectures differ [P9].",
    ]}  # fmt: skip
    uncited = {"paragraphs": ["Graph-based models dominate the field and learn from molecular structure directly."]}
    evaluation = {"paragraphs": ["Most studies evaluate on PDBbind with random splits [P2; P3], which may "
                                 "overstate generalisation [P3]."]}  # fmt: skip

    def frame(prompt: str) -> str:
        assert "1. Graph neural networks for affinity" in prompt and "Cold-target" in prompt
        return json.dumps({
            "opening": "This section reviews learned affinity predictors [P1] in two themes: graph neural "
                       "networks and evaluation practice.",
            "gap_summary": ["Cold-target evaluation appears to be rare among the reviewed studies [P1][P3], which "
                            "may limit claims of generalisation relevant to O2."],
        })  # fmt: skip

    fake.responses = [outline, *([uncited] if bad_first_theme else []), good_graph, evaluation, frame]


async def test_refine_plan_then_write_and_approve_the_literature_survey(
    flow: Flow, llm: FakeLLM, db_session: AsyncSession
) -> None:
    await flow.run("idea")
    await flow.act("idea", "approve")
    p1 = await add_paper(flow, db_session, "GraphDTA: affinity with GNNs", doi="10.1/a")
    p2 = await add_paper(flow, db_session, "SIGN: structure-aware interactions", doi="10.1/b")
    p3 = await add_paper(flow, db_session, "DeepDTA: sequences only", doi="10.1/c", profiled=False)
    await add_paper(flow, db_session, "Unverified preprint: no identifier", doi=None)
    gap = ResearchGap(project_id=flow.pid, statement="Cold-target evaluation appears to be rare among the selected "
                      "papers.", evidence={"signals": [{"paper_ids": [str(p1.id), str(p3.id)]}]},
                      confidence=GapConfidence.MEDIUM, status=GapStatus.ACCEPTED)  # fmt: skip
    db_session.add(gap)
    db_session.add(ManuscriptTable(project_id=flow.pid, section_type=SectionType.LITERATURE_SURVEY, number=1,
                                   caption="Comparison of approaches.",
                                   data={"columns": ["Paper", "Year", "Method"],
                                         "rows": [["GraphDTA: affinity with GNNs", "2022", "GNN"]],
                                         "paper_ids": [str(p1.id)]}))  # fmt: skip
    await db_session.commit()

    # ── Refinements: proposed for accepted gaps; invalid or untraceable ones dropped
    def proposals(prompt: str) -> str:
        assert "G1: Cold-target evaluation" in prompt and "O2: Evaluate ranking quality" in prompt
        return json.dumps({"changes": [
            {"kind": "modify_objective", "target": "O2", "text": "Evaluate ranking quality against baselines, "
             "including cold-target splits.", "gap_ids": ["G1"], "rationale": "The gap concerns cold targets."},
            {"kind": "add_question", "text": "Do GNN ranking gains hold for unseen protein targets?",
             "linked_objective_ids": ["O2"], "gap_ids": ["G1"]},
            {"kind": "modify_question", "target": "RQ9", "text": "Does something else happen here?", "gap_ids": ["G1"]},
            {"kind": "add_question", "text": "Is this change based on any gap at all?", "linked_objective_ids": ["O1"]},
        ]})  # fmt: skip

    llm.responses = [proposals]
    assert (await job_ok(flow, "/literature/refinements/suggest")) == {"gaps": 1, "proposed": 2}
    items = (await flow.client.get(flow.url("/literature/refinements"), headers=flow.headers)).json()
    modify, add = items
    assert modify["kind"] == "modify_objective" and modify["before"] == "Evaluate ranking quality against baselines."
    assert modify["gaps"][0]["statement"].startswith("Cold-target") and add["before"] is None

    # ── The section: thematic, cited, tables kept, gap summary last
    survey_script(llm, bad_first_theme=True)  # the first theme comes back uncited once and is repaired
    await flow.run("literature")
    assert (await flow.statuses())["literature"] == "awaiting_review"
    read = await survey_read(flow)
    text = read["content"]
    assert text.index("### Graph neural networks for affinity") < text.index("### Evaluation practice")
    assert text.index("**Table 1.** Comparison of approaches.") < text.rindex("### Research gaps")
    assert text.rstrip().split("### Research gaps")[1].strip().startswith("Cold-target evaluation appears")
    assert "[P" not in text and f"[@{p1.id}][@{p2.id}]" in text
    assert read["unresolved"] == [] and read["uncited_paragraphs"] == 0
    assert [c["title"] for c in read["citations"]][:2] == [
        "GraphDTA: affinity with GNNs",
        "SIGN: structure-aware interactions",
    ]
    # Tests: every citation key in the section exists in `papers` with a verified id
    keys = set(re.findall(r"\[@([^\]]+)\]", text))
    papers = {str(p.id): p for p in await db_session.scalars(select(Paper).where(Paper.project_id == flow.pid))}
    assert keys == {str(p1.id), str(p2.id), str(p3.id)} and all(survey.verified(papers[k]) for k in keys)

    # ── Accepting a refinement edits the approved plan; the Literature draft becomes stale
    resp = await flow.post(f"/literature/refinements/{add['id']}/accept")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted" and resp.json()["applied_ids"] == ["RQ3"]
    state = await flow.state()
    assert [q["id"] for q in state["state"]["research_questions"]] == ["RQ1", "RQ2", "RQ3"]
    assert state["state"]["stage_status"]["idea"] == "approved"
    assert (await flow.statuses())["literature"] == "stale" and state["pending_review"] is None
    assert "RQ3" in state["state"]["stage_outputs"]["idea"]["draft"]
    again = await flow.post(f"/literature/refinements/{add['id']}/accept")
    assert again.status_code == 409

    # Re-write and approve stage 2
    survey_script(llm)
    await flow.run("literature")
    await flow.act("literature", "approve")
    assert (await flow.statuses())["literature"] == "approved"
    blocked = await flow.post(f"/literature/refinements/{modify['id']}/accept")
    assert blocked.status_code == 409 and "Go back" in blocked.json()["detail"]

    # ── The analysis report
    resp = await flow.client.get(
        flow.url("/literature/report"), headers={**flow.headers, "Origin": "http://localhost:3000"}
    )
    assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
    assert "Content-Disposition" in resp.headers["access-control-expose-headers"]  # the browser can read the filename
    report = resp.text
    for heading in ("## Overview", "## Selected papers", "## Paper analyses", "## Potential research gaps"):
        assert heading in report
    assert "GraphDTA: affinity with GNNs" in report and "**Accepted · medium confidence.** Cold-target" in report
    assert "_Not analysed (no full text)._" in report  # DeepDTA has no profile


async def test_survey_needs_verified_papers_and_refinements_need_accepted_gaps(
    flow: Flow, llm: FakeLLM, db_session: AsyncSession
) -> None:
    await flow.run("idea")
    await flow.act("idea", "approve")
    assert (await flow.post("/literature/refinements/suggest")).status_code == 409
    await add_paper(flow, db_session, "Unverified preprint: no identifier", doi=None)
    resp = await flow.post("/stages/literature/run")
    assert resp.status_code == 202
    job_id, job_type, params = flow.dispatcher.queued.pop(0)
    assert await execute_job(job_id, HANDLERS[job_type], params, factory_for(flow.db)) == JobStatus.FAILED
    job = (await flow.client.get(f"/api/v1/jobs/{job_id}", headers=flow.headers)).json()
    assert "verified identifier" in job["error"]
    assert (await flow.statuses())["literature"] != "awaiting_review"

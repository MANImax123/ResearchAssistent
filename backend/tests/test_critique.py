"""Step 3.2: parallel critics, suggestion rules, revision applies ONLY selected suggestions, API flow."""

import asyncio
import copy
import time
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.idea.critique import (
    Critique,
    Suggestion,
    allowed_fields,
    make_llm_critic,
    merge_revision,
    run_critiques,
)
from app.agents.idea.schemas import IdeaPlan
from app.core.config import get_settings
from app.db.models import Feedback
from app.db.models.enums import StageName
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from tests.helpers import Flow
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})


def critique(prefix: str, *affects: list[str], score: int = 3) -> dict[str, Any]:
    return {
        "score": score,
        "summary": f"{prefix} review of the plan with specific remarks on O2 and RQ1.",
        "strengths": [f"{prefix}: RQ1 is clearly linked to O1 and O2."],
        "risks": [f"{prefix}: O3 'Analyse failure cases' has no stated method."],
        "suggestions": [
            {"title": f"{prefix} change {i + 1}", "detail": f"Change {', '.join(a)} as described for O2.", "affects": a}
            for i, a in enumerate(affects)
        ],
    }


CRITIQUES = {
    "novelty": critique("Novelty", ["expected_contributions"], ["refined_idea"], score=2),
    "feasibility": critique("Feasibility", ["scope"], score=4),
    "methodology": critique("Methodology", ["objectives"], ["research_questions"], score=3),
}


# ─── Parallel execution ─────────────────────────────────────────────────


async def test_critics_run_in_parallel() -> None:
    spans: dict[str, tuple[float, float]] = {}

    def slow(name: str) -> Any:
        async def critic(plan_json: str, project_id: str) -> tuple[Critique, str]:
            start = time.perf_counter()
            await asyncio.sleep(0.3)
            spans[name] = (start, time.perf_counter())
            return Critique.model_validate(CRITIQUES[name]), f"run-{name}"

        return critic

    began = time.perf_counter()
    result = await run_critiques(
        IdeaPlan.model_validate(PLAN), uuid.uuid4(), {n: slow(n) for n in ("novelty", "feasibility", "methodology")}
    )
    elapsed = time.perf_counter() - began

    assert elapsed < 0.8  # sequential would be ≥ 0.9 s
    first_end = min(end for _, end in spans.values())
    assert all(start < first_end for start, _ in spans.values())  # all started before any finished
    assert list(result.critiques) == ["novelty", "feasibility", "methodology"]
    assert [s.id for s in result.critiques["novelty"].suggestions] == ["N1", "N2"]
    assert [s.id for s in result.critiques["methodology"].suggestions] == ["M1", "M2"]
    assert result.round == 1 and result.agent_run_ids["feasibility"] == "run-feasibility"


def test_suggestions_must_name_real_plan_fields() -> None:
    with pytest.raises(ValidationError):
        Suggestion(title="Fix it", detail="Change the budget section.", affects=["budget"])  # type: ignore[list-item]
    s = Suggestion(title="Tighten", detail="Narrow O2 to two datasets.", affects=["objectives", "objectives", "scope"])
    assert s.affects == ["objectives", "scope"]


# ─── Revision applies ONLY the selected suggestions ──────────────────────


def everything_changed() -> IdeaPlan:
    """A 'revision' that rewrote every field: the merge must keep only the allowed ones."""
    data = copy.deepcopy(PLAN)
    for key in ("refined_idea", "problem_statement", "motivation", "scope", "domain"):
        data[key] = f"REWRITTEN {key}"
    data["keywords"] = ["new", "key", "words"]
    data["expected_contributions"] = ["REWRITTEN contribution 1", "REWRITTEN contribution 2"]
    data["objectives"][1]["text"] = "REWRITTEN objective O2 with two datasets."
    data["research_directions"] = ["REWRITTEN direction 1", "REWRITTEN direction 2"]
    data["title_options"] = ["New Title One", "New Title Two", "New Title Three"]
    return IdeaPlan.model_validate(data)


def test_merge_keeps_unselected_fields_identical() -> None:
    current = IdeaPlan.model_validate({**PLAN, "title": PLAN["title_options"][0]})
    selected = [
        Suggestion(id="F1", title="Narrow scope", detail="Exclude proteins.", affects=["scope"]),
        Suggestion(id="N1", title="Sharpen", detail="State novelty.", affects=["expected_contributions"]),
    ]

    merged = merge_revision(current, everything_changed(), allowed_fields(selected))

    assert merged.scope == "REWRITTEN scope"
    assert merged.expected_contributions == ["REWRITTEN contribution 1", "REWRITTEN contribution 2"]
    changed = {k for k, v in merged.model_dump().items() if v != current.model_dump()[k]}
    assert changed == {"scope", "expected_contributions"}


def test_objectives_and_questions_move_together() -> None:
    assert allowed_fields([Suggestion(title="Refine", detail="Refine O2 wording.", affects=["objectives"])]) == {
        "objectives",
        "research_questions",
    }


def test_title_follows_options_unless_the_researcher_wrote_it() -> None:
    fields = {"title_options"}
    ai_title = IdeaPlan.model_validate({**PLAN, "title": PLAN["title_options"][1]})
    assert merge_revision(ai_title, everything_changed(), fields).title == "New Title One"
    own_title = IdeaPlan.model_validate({**PLAN, "title": "My Own Title"})
    assert merge_revision(own_title, everything_changed(), fields).title == "My Own Title"


# ─── Critic agent ─────────────────────────────────────────────────────


async def test_llm_critic_gets_the_plan_and_rules(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    fake = FakeLLM([CRITIQUES["methodology"]])
    critic = make_llm_critic("methodology", lambda: LLMService(fake, SETTINGS, _factory(db_session)))

    result, run_id = await critic(IdeaPlan.model_validate(PLAN).model_dump_json(), str(project_id))

    assert result.score == 3 and run_id
    call = fake.calls[0]
    assert "Design a GNN that predicts binding affinity." in call.prompt
    assert call.system is not None and "METHODOLOGY critic" in call.system and "Do not cite papers" in call.system
    assert call.json_schema is not None and call.json_schema["title"] == "Critique"


def _factory(session: AsyncSession) -> Any:
    from tests.helpers import factory_for

    return factory_for(session)


# ─── API flow ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_llms(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> dict[str, FakeLLM]:
    """FakeLLMs for the idea agent, each critic, and the revision."""
    fakes = {name: FakeLLM() for name in ("idea", "novelty", "feasibility", "methodology", "revise")}

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    def llm(name: str, session_factory: Any) -> Any:
        return lambda: LLMService(fakes[name], SETTINGS, session_factory)

    monkeypatch.setattr(
        registry, "agent_factory", lambda sf: {StageName.IDEA: make_idea_agent(llm("idea", sf), no_notes)}
    )
    monkeypatch.setattr(
        registry,
        "critic_factory",
        lambda sf: {n: make_llm_critic(n, llm(n, sf)) for n in ("novelty", "feasibility", "methodology")},  # type: ignore[arg-type]
    )
    monkeypatch.setattr(registry, "llm_factory", lambda sf: llm("revise", sf))
    return fakes


async def plan_under_review(flow: Flow, fakes: dict[str, FakeLLM]) -> None:
    fakes["idea"].responses.append(PLAN)
    await flow.run("idea")


async def test_critique_then_apply_two_of_five(
    flow: Flow, fake_llms: dict[str, FakeLLM], db_session: AsyncSession
) -> None:
    """Acceptance: applying 2 of 5 suggestions changes only the relevant fields."""
    await plan_under_review(flow, fake_llms)
    for name, data in CRITIQUES.items():
        fake_llms[name].responses.append(data)

    started = await flow.post("/idea/critiques")
    assert started.status_code == 202 and started.json()["type"] == "idea_critique"
    await flow.finish_jobs()

    ws = (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()
    crit = ws["critiques"]
    assert crit["round"] == 1 and list(crit["critiques"]) == ["novelty", "feasibility", "methodology"]
    ids = [s["id"] for c in crit["critiques"].values() for s in c["suggestions"]]
    assert ids == ["N1", "N2", "F1", "M1", "M2"]  # five suggestions
    assert crit["critiques"]["novelty"]["score"] == 2
    before = ws["plan"]

    fake_llms["revise"].responses.append(everything_changed().model_dump())
    resp = await flow.post("/idea/revise", {"suggestion_ids": ["F1", "N1"]})
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()

    after = (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()
    changed = {k for k in before if before[k] != after["plan"][k]}
    assert changed == {"scope", "expected_contributions"}
    assert after["critiques"]["applied"] == ["F1", "N1"]
    assert after["status"] == "awaiting_review"

    prompt = fake_llms["revise"].calls[0].prompt
    assert "F1 [changes: scope]" in prompt and "N1 [changes: expected_contributions]" in prompt
    assert "M1" not in prompt and "Fields you may change: expected_contributions, scope" in prompt
    note = await db_session.scalar(select(Feedback.comment).where(Feedback.project_id == flow.pid))
    assert note == "Applied critic suggestions F1, N1"


async def test_revise_rules(flow: Flow, fake_llms: dict[str, FakeLLM]) -> None:
    await plan_under_review(flow, fake_llms)
    assert (await flow.post("/idea/revise", {"suggestion_ids": ["N1"]})).status_code == 409  # no critiques yet

    for name, data in CRITIQUES.items():
        fake_llms[name].responses.append(data)
    await flow.post("/idea/critiques")
    assert (await flow.post("/idea/critiques")).status_code == 409  # one critique job at a time
    await flow.finish_jobs()

    assert (await flow.post("/idea/revise", {"suggestion_ids": ["N9"]})).status_code == 422
    assert (await flow.post("/idea/revise", {"suggestion_ids": ["X1"]})).status_code == 422
    assert (await flow.post("/idea/revise", {"suggestion_ids": []})).status_code == 422

    await flow.act("idea", "approve")
    assert (await flow.post("/idea/critiques")).status_code == 409  # approved: go back first


async def test_new_round_keeps_counting(flow: Flow, fake_llms: dict[str, FakeLLM]) -> None:
    await plan_under_review(flow, fake_llms)
    for _ in range(2):
        for name, data in CRITIQUES.items():
            fake_llms[name].responses.append(data)
        await flow.post("/idea/critiques")
        await flow.finish_jobs()

    ws = (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()
    assert ws["critiques"]["round"] == 2


async def test_new_plan_clears_critiques(flow: Flow, fake_llms: dict[str, FakeLLM]) -> None:
    """Suggestions name O#/RQ# ids of the plan they reviewed; a regenerated plan must not inherit them."""
    await plan_under_review(flow, fake_llms)
    for name, data in CRITIQUES.items():
        fake_llms[name].responses.append(data)
    await flow.post("/idea/critiques")
    await flow.finish_jobs()

    fake_llms["idea"].responses.append(PLAN)
    await flow.act("idea", "regenerate", {"feedback": "Narrow the scope"}, expect=202)
    await flow.finish_jobs()

    ws = (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()
    assert ws["status"] == "awaiting_review" and ws["critiques"] is None
    assert (await flow.post("/idea/revise", {"suggestion_ids": ["N1"]})).status_code == 409

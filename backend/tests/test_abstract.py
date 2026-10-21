"""Step 3.3: working abstract — length control, objective coverage, section version, approve as a whole."""

import copy
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.abstract import MAX_ATTEMPTS, _clean, write_abstract
from app.agents.idea.agent import make_idea_agent
from app.agents.idea.schemas import IdeaPlan
from app.core.config import get_settings
from app.core.venues import DEFAULT_ABSTRACT_WORDS, abstract_word_limits
from app.db.models.enums import StageName
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from app.services.sections import word_count
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})
ALL = ["O1", "O2", "O3"]


def abstract(words: int, covered: list[str] = ALL) -> dict[str, Any]:
    body = " ".join(f"word{i}" for i in range(words - 1))
    return {"abstract": f"{body} end.", "objectives_covered": covered}


# ─── Venue limits & cleanup ─────────────────────────────────────────────


def test_word_limits_come_from_the_venue_or_the_default() -> None:
    assert abstract_word_limits("ieee") == (150, 250)
    assert abstract_word_limits("springer_lncs") == (150, 250)
    assert abstract_word_limits("elsevier") == DEFAULT_ABSTRACT_WORDS == (200, 250)
    assert abstract_word_limits(None) == (200, 250)


def test_abstract_is_cleaned_to_one_plain_paragraph() -> None:
    raw = "**Abstract:** Graph models *rank* molecules.\n\nWe evaluate `them`."
    assert _clean(raw) == "Graph models rank molecules. We evaluate them."
    assert word_count("Graph-based models [@smith2020] rank O'Neil's molecules.") == 5


# ─── Length control ─────────────────────────────────────────────────────


async def _write(db_session: AsyncSession, project_id: uuid.UUID, responses: list[Any], limits=(200, 250)):  # type: ignore[no-untyped-def]
    fake = FakeLLM(responses=responses)
    llm = LLMService(fake, SETTINGS, factory_for(db_session))
    result = await write_abstract(IdeaPlan.model_validate(PLAN), llm, project_id, limits, "ieee")
    return result, fake


async def test_first_draft_within_range_is_kept(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    result, fake = await _write(db_session, project_id, [abstract(220)])

    assert (result.word_count, result.within_limit, result.attempts) == (220, True, 1)
    prompt = fake.calls[0].prompt
    assert "Length: 200-250 words." in prompt and "Target venue: ieee" in prompt
    assert all(o["text"] in prompt for o in PLAN["objectives"])  # written from the plan's objectives


async def test_too_long_or_missing_objective_is_rewritten(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    result, fake = await _write(db_session, project_id, [abstract(310, ["O1", "O2"]), abstract(240)])

    assert (result.word_count, result.within_limit, result.attempts) == (240, True, 2)
    fix = fake.calls[1].prompt
    assert "It has 310 words; it must have at most 250. Cut 60 or more words." in fix
    assert "does not address these objectives: O3: Analyse failure cases by molecular family." in fix
    assert len(result.run_ids) == 2


async def test_best_attempt_is_kept_when_range_is_never_met(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    result, fake = await _write(db_session, project_id, [abstract(400), abstract(120), abstract(270)])

    assert len(fake.calls) == MAX_ATTEMPTS
    assert (result.word_count, result.within_limit) == (270, False)  # closest to 200-250
    assert "at least 200" in fake.calls[2].prompt


# ─── Workspace flow ─────────────────────────────────────────────────────


@pytest.fixture
def llms(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeLLM]:
    """FakeLLMs for the Idea agent and the abstract writer."""
    fakes = {"idea": FakeLLM(), "abstract": FakeLLM()}

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    def llm(name: str, session_factory: Any) -> Any:
        return lambda: LLMService(fakes[name], SETTINGS, session_factory)

    monkeypatch.setattr(
        registry, "agent_factory", lambda sf: {StageName.IDEA: make_idea_agent(llm("idea", sf), no_notes)}
    )
    monkeypatch.setattr(registry, "llm_factory", lambda sf: llm("abstract", sf))
    return fakes


async def workspace(flow: Flow) -> dict[str, Any]:
    return (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()  # type: ignore[no-any-return]


async def test_draft_edit_and_approve_stage_one(flow: Flow, llms: dict[str, FakeLLM]) -> None:
    """Acceptance: abstract reflects the objectives, is editable, and stage 1 is approved as a whole."""
    ab = (await workspace(flow))["abstract"]
    assert ab["content"] is None and (ab["min_words"], ab["max_words"]) == (200, 250)
    assert (await flow.post("/idea/abstract")).status_code == 409  # no plan yet

    llms["idea"].responses.append(PLAN)
    await flow.run("idea")
    llms["abstract"].responses.append(abstract(230))
    started = await flow.post("/idea/abstract")
    assert started.status_code == 202 and started.json()["type"] == "idea_abstract"
    assert (await flow.post("/idea/abstract")).status_code == 409  # one plan job at a time
    await flow.finish_jobs()

    ab = (await workspace(flow))["abstract"]
    assert ab["word_count"] == 230 and ab["content"].endswith("end.")
    assert ab["edited"] is False and ab["outdated"] is False and ab["generated_at"]
    section = (await flow.client.get(flow.url("/sections/abstract"), headers=flow.headers)).json()
    assert section["current_version"]["source"] == "ai" and section["status"] == "draft" and section["editable"]
    assert section["current_version"]["change_note"] == "Drafted by idea.abstract agent (230 words)"
    state = (await flow.state())["state"]
    assert state["abstract_draft"] == ab["content"] and state["abstract_meta"]["within_limit"] is True

    # The researcher edits it in place
    body = {"content": "My own abstract. " * 20, "base_version_id": section["current_version"]["id"]}
    assert (await flow.client.post(flow.url("/sections/abstract/versions"), json=body, headers=flow.headers)).is_success
    ab = (await workspace(flow))["abstract"]
    assert ab["edited"] is True and ab["word_count"] == 60

    # Changing the plan flags the abstract as written from an older plan (a new title doesn't)
    plan = copy.deepcopy((await workspace(flow))["plan"])
    plan["title"] = PLAN["title_options"][1]
    await flow.client.put(flow.url("/idea/plan"), json=plan, headers=flow.headers)
    assert (await workspace(flow))["abstract"]["outdated"] is False
    plan["objectives"][2]["text"] = "Analyse failure cases by scaffold."
    await flow.client.put(flow.url("/idea/plan"), json=plan, headers=flow.headers)
    assert (await workspace(flow))["abstract"]["outdated"] is True

    # Approving the stage approves plan and abstract together; both are then locked
    await flow.act("idea", "approve")
    section = (await flow.client.get(flow.url("/sections/abstract"), headers=flow.headers)).json()
    assert section["status"] == "approved" and section["editable"] is False
    assert (await flow.post("/idea/abstract")).status_code == 409


async def test_venue_sets_the_target(flow: Flow, llms: dict[str, FakeLLM]) -> None:
    await flow.client.patch(flow.url(""), json={"target_venue": "ieee"}, headers=flow.headers)
    llms["idea"].responses.append(PLAN)
    await flow.run("idea")
    llms["abstract"].responses.append(abstract(180))
    await flow.post("/idea/abstract")
    await flow.finish_jobs()

    ab = (await workspace(flow))["abstract"]
    assert (ab["min_words"], ab["max_words"], ab["word_count"]) == (150, 250, 180)
    assert "Target venue: ieee" in llms["abstract"].calls[0].prompt

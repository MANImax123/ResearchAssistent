"""Step 5.1: methodology input workspace — the form, what's missing, uploads, follow-up questions.

Acceptance: the agent's follow-up questions are about what is actually missing (or quote something the
researcher wrote that is too vague); questions about things the form already covers are dropped.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.methodology.schemas import DatasetInfo, MethodologyInputData, Metric, Tool, missing
from app.core.config import get_settings
from app.db.models.enums import StageName
from app.services.context import extract_text
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from tests.conftest import TEST_STORAGE
from tests.helpers import Flow, auth_headers
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})


# ─── Completeness check ─────────────────────────────────────────────────


def test_empty_input_misses_the_essentials_first() -> None:
    fields = [m.field for m in missing(MethodologyInputData())]
    assert fields[:4] == ["algorithms", "datasets", "experimental_setup", "metrics"]
    assert set(fields) == {"algorithms", "datasets", "experimental_setup", "metrics", "architecture", "workflow",
                           "preprocessing", "hyperparameters", "tools", "reproducibility"}  # fmt: skip


def test_partial_details_are_pointed_out() -> None:
    data = MethodologyInputData(
        algorithms="A 3-layer GCN over molecular graphs.",
        datasets=[DatasetInfo(name="Davis", source="Davis et al. 2011", size="30,056 pairs"), DatasetInfo(name="KIBA")],
        experimental_setup="We compare against random forests on ECFP4 fingerprints.",
        metrics=[Metric(name="RMSE")],
        tools=[Tool(name="PyTorch Geometric", version="2.5")],
    )
    reasons = {m.field: m.reason for m in missing(data)}
    assert reasons["datasets"] == "Dataset “KIBA” has no source or size."
    assert "split" in reasons["experimental_setup"]
    assert "metrics" not in reasons and "algorithms" not in reasons and "tools" not in reasons
    done = data.model_copy(update={"experimental_setup": "5-fold cross-validation with a held-out test set."})
    assert "experimental_setup" not in {m.field for m in missing(done)}


def test_uploads_count_for_architecture_and_reproducibility() -> None:
    fields = {m.field for m in missing(MethodologyInputData(), diagrams=1, code_files=2)}
    assert "architecture" not in fields and "reproducibility" not in fields


def test_code_and_notebooks_are_read_as_context() -> None:
    assert extract_text(b"def train():\n    pass\n", "text/x-python").startswith("def train")
    notebook = {"cells": [{"cell_type": "markdown", "source": ["# Setup\n", "Seed 42"]},
                          {"cell_type": "code", "source": "model.fit(X)"}]}  # fmt: skip
    assert extract_text(json.dumps(notebook).encode(), "application/x-ipynb+json") == "# Setup\nSeed 42\n\nmodel.fit(X)"


# ─── API flow ───────────────────────────────────────────────────────────


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch, storage: Any) -> FakeLLM:
    fake = FakeLLM()
    idea = FakeLLM(responses=[PLAN])

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    monkeypatch.setattr(
        registry, "agent_factory",
        lambda sf: {StageName.IDEA: make_idea_agent(lambda: LLMService(idea, SETTINGS, sf), no_notes)},
    )  # fmt: skip
    monkeypatch.setattr(registry, "llm_factory", lambda sf: lambda: LLMService(fake, SETTINGS, sf))
    monkeypatch.setattr(registry, "storage_factory", lambda: TEST_STORAGE)
    return fake


INPUT = {
    "algorithms": "A 3-layer GCN over molecular graphs, trained with some standard settings.",
    "datasets": [{"name": "Davis", "source": "Davis et al. 2011", "size": "30,056 pairs"}, {"name": "KIBA"}],
    "experimental_setup": "We compare against random forests on ECFP4 fingerprints.",
    "tools": [{"name": "PyTorch Geometric", "version": "2.5"}],
}


async def workspace(flow: Flow, headers: dict[str, str] | None = None) -> Any:
    return await flow.client.get(flow.url("/methodology"), headers=headers or flow.headers)


async def save(flow: Flow, data: dict[str, Any], base: int | None) -> Any:
    return await flow.client.put(flow.url("/methodology"), json={"input": data, "base_version": base},
                                 headers=flow.headers)  # fmt: skip


async def follow_ups(flow: Flow) -> dict[str, Any]:
    resp = await flow.post("/methodology/follow-ups")
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


async def test_follow_up_questions_are_about_what_is_missing(
    flow: Flow, llm: FakeLLM, db_session: AsyncSession
) -> None:
    ws = (await workspace(flow)).json()
    assert ws["editable"] is False and ws["blocked_reason"].startswith("Approve the research plan")
    assert (await save(flow, INPUT, 0)).status_code == 409
    await flow.run("idea")
    await flow.act("idea", "approve")

    resp = await save(flow, INPUT, 0)
    assert resp.status_code == 200, resp.text
    ws = resp.json()
    assert ws["version"] == 1 and ws["editable"] and ws["input"]["datasets"][1] == {
        "name": "KIBA", "source": "", "size": "", "features": "", "notes": ""
    }  # fmt: skip
    missing_now = {m["field"]: m for m in ws["missing"]}
    assert missing_now["metrics"]["essential"] and "split" in missing_now["experimental_setup"]["reason"]
    assert "algorithms" not in missing_now
    stale = await save(flow, INPUT, 0)  # edited from an older version
    assert stale.status_code == 409 and "version 1" in stale.json()["detail"]
    state = (await flow.state())["state"]["methodology"]
    assert state["algorithms"].startswith("A 3-layer GCN") and state["clarifications"] == []

    upload = await flow.client.post(
        flow.url("/files"), data={"kind": "notes"}, headers=flow.headers,
        files={"file": ("notes.md", b"Training uses a learning rate of 0.001 and batch size 256.", "text/markdown")},
    )  # fmt: skip
    assert upload.status_code == 201, upload.text

    def answer(prompt: str) -> str:
        assert "- metrics (Evaluation metrics, essential): No evaluation metric is given." in prompt
        assert "learning rate of 0.001" in prompt  # the uploaded notes are context
        assert "- algorithms" not in prompt  # not missing, so not on the checklist
        return json.dumps({"questions": [
            {"field": "metrics", "question": "Which metrics do you report for affinity on Davis and KIBA?",
             "reason": "RQ1 compares ranking quality."},
            {"field": "experimental_setup", "question": "How do you split Davis and KIBA into train and test?"},
            {"field": "datasets", "question": "Where does your KIBA data come from, and how many pairs?"},
            {"field": "algorithms", "question": "Which model architecture do you use for the drugs?"},
            {"field": "algorithms", "kind": "unclear", "quote": "some standard settings",
             "question": "Which optimiser, learning rate and epochs are the 'standard settings'?"},
            {"field": "tools", "kind": "unclear", "quote": "TensorFlow 1.x",
             "question": "Why do you use TensorFlow 1.x?"},
            {"field": "banana", "question": "What is the meaning of this field at all?"},
            {"field": "metrics", "question": "Which metrics do you report for affinity on Davis and KIBA? "},
        ]})  # fmt: skip

    llm.responses = [answer]
    result = await follow_ups(flow)
    assert result["asked"] == 4
    questions = (await workspace(flow)).json()["questions"]
    assert [(q["field"], q["kind"]) for q in questions] == [
        ("metrics", "missing"), ("experimental_setup", "missing"), ("datasets", "missing"), ("algorithms", "unclear")
    ]  # fmt: skip
    assert "You wrote: “some standard settings”" in questions[3]["reason"]

    # Answer one, dismiss another; answers join the state's methodology block
    metrics_q, split_q = questions[0], questions[1]
    bad = await flow.client.patch(flow.url(f"/methodology/questions/{metrics_q['id']}"), json={"status": "answered"},
                                  headers=flow.headers)  # fmt: skip
    assert bad.status_code == 422
    resp = await flow.client.patch(flow.url(f"/methodology/questions/{metrics_q['id']}"),
                                   json={"status": "answered", "answer": "RMSE, CI and Pearson r."},
                                   headers=flow.headers)  # fmt: skip
    assert resp.status_code == 200 and resp.json()["status"] == "answered"
    await flow.client.patch(flow.url(f"/methodology/questions/{split_q['id']}"), json={"status": "dismissed"},
                            headers=flow.headers)  # fmt: skip
    state = (await flow.state())["state"]["methodology"]
    assert state["clarifications"] == [{"field": "metrics", "question": metrics_q["question"],
                                        "answer": "RMSE, CI and Pearson r."}]  # fmt: skip

    # A re-run replaces open questions, keeps answered ones and tells the agent about them
    def again(prompt: str) -> str:
        assert "A: RMSE, CI and Pearson r." in prompt
        return json.dumps({"questions": [
            {"field": "metrics", "question": "Which metrics do you report for affinity on Davis and KIBA?"},
            {"field": "preprocessing", "question": "How are SMILES strings standardised before building graphs?"},
        ]})  # fmt: skip

    llm.responses = [again]
    assert (await follow_ups(flow))["asked"] == 1  # the repeated metrics question is dropped
    statuses = [(q["field"], q["status"]) for q in (await workspace(flow)).json()["questions"]]
    assert statuses == [("preprocessing", "open"), ("metrics", "answered")]

    bob = await auth_headers(flow.client, "bob-method@example.com")
    assert (await workspace(flow, bob)).status_code == 404
    other = await flow.client.patch(flow.url(f"/methodology/questions/{metrics_q['id']}"),
                                    json={"status": "open"}, headers=bob)  # fmt: skip
    assert other.status_code == 404


async def test_nothing_missing_means_no_questions(flow: Flow, llm: FakeLLM) -> None:
    await flow.run("idea")
    await flow.act("idea", "approve")
    full = {
        **INPUT,
        "algorithms": "A 3-layer GCN (hidden size 128, mean pooling) over RDKit molecular graphs.",
        "datasets": [{"name": "Davis", "source": "Davis et al. 2011", "size": "30,056 pairs"}],
        "experimental_setup": "5-fold cross-validation; a held-out test set of 20% by drug scaffold.",
        "metrics": [{"name": "RMSE"}], "architecture": "Encoder + MLP head.", "workflow": "Featurise, train, rank.",
        "preprocessing": "Canonical SMILES; salts removed.", "hyperparameters": [{"name": "lr", "value": "0.001"}],
        "reproducibility": "Code on GitHub; seeds 0–4.",
    }  # fmt: skip
    assert (await save(flow, full, 0)).json()["missing"] == []
    llm.responses = [lambda prompt: json.dumps({"questions": [
        {"field": "metrics", "question": "Which metrics do you report for this study?"}]})]  # fmt: skip
    assert (await follow_ups(flow))["asked"] == 0
    assert "Missing checklist:\n(nothing)" in llm.calls[-1].prompt

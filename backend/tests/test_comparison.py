"""Step 4.6: comparison matrix — grounded cells, researcher edits kept, custom columns, CSV, insertion."""

import csv
import io
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry
from app.agents.idea.agent import make_idea_agent
from app.agents.literature.comparison import PaperFacts, build_rows, grounded
from app.core.config import get_settings
from app.db.models import ManuscriptTable, Paper, PaperChunk, PaperProfile
from app.db.models.enums import PaperOrigin, ParseStatus, StageName
from app.services.comparison import to_csv, to_markdown
from app.services.embeddings import EmbeddingService, FakeEmbeddings
from app.services.llm import LLMService
from app.services.llm_providers import FakeLLM
from tests.helpers import Flow, factory_for
from tests.test_idea import PLAN

SETTINGS = get_settings().model_copy(update={"llm_model_fallback": "", "llm_max_retries": 0})


def row(pid: str, **cells: str) -> dict[str, Any]:
    return {"paper": pid, **cells}


# ─── Units ──────────────────────────────────────────────────────────────


def test_numbers_must_come_from_the_source() -> None:
    assert grounded("RMSE 1.316, R 0.797", "SIGN reaches RMSE 1.316 and Pearson R 0.797")
    assert not grounded("RMSE 1.2", "SIGN reaches RMSE 1.316")
    assert grounded("GNN with attention", "")


async def test_cells_are_condensed_but_grounded(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    profiled = PaperFacts(
        uuid.uuid4(), "SIGN", 2021,
        {"method": "Structure-aware GNN with polar attention and pairwise pooling.", "dataset": ["PDBbind"],
         "metrics": ["RMSE"], "results": "RMSE 1.316 on the core set.", "limitations": []},
        {"method": ["c1"], "dataset": [["c2"]], "metrics": [["c3"]], "results": ["c4"]},
    )  # fmt: skip
    abstract_only = PaperFacts(uuid.uuid4(), "Closed paper", 2022, None, {}, "We predict affinity with a CNN.")
    fake = FakeLLM(
        responses=[
            {"rows": [row("P1", method="SIGN GNN", dataset="PDBbind", metric="RMSE", result="RMSE 1.2 on core",
                          limitation="Needs 3D structures"),
                      row("P2", method="CNN affinity model")]}
        ]
    )  # fmt: skip
    built = await build_rows([profiled, abstract_only], LLMService(fake, SETTINGS, factory_for(db_session)), project_id)

    sign = built[profiled.paper_id]
    assert (
        sign["method"].value == "SIGN GNN" and sign["method"].evidence == ["c1"] and sign["method"].source == "profile"
    )
    assert sign["result"].value == "RMSE 1.316 on the core set."  # invented "1.2": the profile's own text instead
    assert "limitation" not in sign  # the profile states none, so the model's claim is dropped
    closed = built[abstract_only.paper_id]
    assert closed["method"].value == "CNN affinity model" and closed["method"].source == "abstract"
    assert closed["method"].evidence == [] and "dataset" not in closed


def test_csv_opens_in_spreadsheets_and_markdown_cites_papers() -> None:
    header = ["Paper", "Year", "Result"]
    body = [['SIGN: "structure-aware", GNN', "2021", "RMSE 1.316\nR 0.797"], ["A | B", "", ""]]
    text = to_csv(header, body)
    assert text.startswith("﻿") and "\r\n" in text
    assert list(csv.reader(io.StringIO(text.lstrip("﻿")))) == [header, *body]

    papers = [Paper(id=uuid.uuid4(), title="SIGN"), Paper(id=uuid.uuid4(), title="A | B")]
    md = to_markdown(header, body, papers).splitlines()
    assert md[0] == "| Paper | Year | Result |" and md[1] == "|---|---|---|"
    assert md[2].startswith(f'| SIGN: "structure-aware", GNN [@{papers[0].id}] | 2021 | RMSE 1.316 R 0.797 |')
    assert f"A \\| B [@{papers[1].id}]" in md[3]


# ─── API flow ───────────────────────────────────────────────────────────


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    idea = FakeLLM(responses=[PLAN])

    async def no_notes(_: uuid.UUID) -> str:
        return ""

    monkeypatch.setattr(
        registry, "agent_factory",
        lambda sf: {StageName.IDEA: make_idea_agent(lambda: LLMService(idea, SETTINGS, sf), no_notes)},
    )  # fmt: skip
    monkeypatch.setattr(registry, "llm_factory", lambda sf: lambda: LLMService(fake, SETTINGS, sf))
    monkeypatch.setattr(registry, "embeddings_factory", lambda: EmbeddingService(FakeEmbeddings(), SETTINGS))
    return fake


async def library(flow: Flow, db_session: AsyncSession) -> dict[str, uuid.UUID]:
    await flow.run("idea")
    await flow.act("idea", "approve")
    ids = {}
    for key, title, parsed in [("sign", "SIGN", True), ("dta", "GraphDTA", True), ("closed", "Closed", False)]:
        paper = Paper(project_id=flow.pid, title=title, year=2021, origin=PaperOrigin.SEARCH, sources=[], authors=[],
                      selected=True, abstract=f"{title} predicts binding affinity.",
                      parse_status=ParseStatus.PARSED if parsed else ParseStatus.UPLOAD_NEEDED)  # fmt: skip
        db_session.add(paper)
        await db_session.flush()
        ids[key] = paper.id
        if parsed:
            text = f"{title} was trained on four NVIDIA V100 GPUs (hardware) using PDBbind."
            [vector] = await EmbeddingService(FakeEmbeddings(), SETTINGS).embed_documents([text])
            chunk = PaperChunk(paper_id=paper.id, chunk_index=0, section_name="5 Setup", section_kind="dataset",
                               page=6, text=text, embedding=vector)  # fmt: skip
            db_session.add(chunk)
            await db_session.flush()
            cid = str(chunk.id)
            evidence = {"method": [cid], "results": [cid], "dataset": [[cid]], "metrics": [[cid]]}
            db_session.add(PaperProfile(paper_id=paper.id, method=f"{title} graph network.", results="RMSE 1.3.",
                                        dataset=["PDBbind"], metrics=["RMSE"], limitations=[], tech_stack=[],
                                        evidence=evidence))  # fmt: skip
    await db_session.commit()
    return ids


async def matrix(flow: Flow) -> dict[str, Any]:
    resp = await flow.client.get(flow.url("/literature/comparison"), headers=flow.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def run_job(flow: Flow, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = await flow.post(path, body or {})
    assert resp.status_code == 202, resp.text
    await flow.finish_jobs()
    job = (await flow.client.get(f"/api/v1/jobs/{resp.json()['id']}", headers=flow.headers)).json()
    assert job["status"] == "success", job
    return job["result"]  # type: ignore[no-any-return]


def cells(m: dict[str, Any], title: str) -> dict[str, Any]:
    return next(r["cells"] for r in m["rows"] if r["paper"]["title"] == title)


async def test_matrix_covers_selection_keeps_edits_and_exports(
    flow: Flow, llm: FakeLLM, db_session: AsyncSession
) -> None:
    """Acceptance (with fakes): covers all selected papers; edits persist; CSV export opens correctly."""
    ids = await library(flow, db_session)
    m = await matrix(flow)
    assert [c["key"] for c in m["columns"]] == ["method", "dataset", "metric", "result", "limitation"]
    assert len(m["rows"]) == 3 and m["uncompared"] == 3

    def answer(prompt: str) -> dict[str, Any]:
        ids_ = re.findall(r"^(P\d+) — (\w+)", prompt, flags=re.MULTILINE)
        return {"rows": [row(pid, method=f"{name} model", dataset="PDBbind", metric="RMSE", result="RMSE 1.3")
                         for pid, name in ids_]}  # fmt: skip

    llm.responses.append(answer)
    assert await run_job(flow, "/literature/comparison/build") == {"papers": 3, "compared": 3}
    m = await matrix(flow)
    assert m["uncompared"] == 0
    sign = cells(m, "SIGN")
    assert sign["method"]["value"] == "SIGN model" and sign["method"]["source"] == "profile"
    assert sign["method"]["evidence"][0]["section"] == "5 Setup" and sign["method"]["evidence"][0]["page"] == 6
    assert cells(m, "Closed")["method"]["source"] == "abstract"

    # The researcher's edit persists through a forced rebuild
    edit = {"paper_id": str(ids["sign"]), "key": "result", "value": "RMSE 1.316, Pearson R 0.797"}
    assert (await flow.client.put(flow.url("/literature/comparison/cells"), json=edit, headers=flow.headers)).is_success
    llm.responses.append(answer)
    await run_job(flow, "/literature/comparison/build", {"force": True})
    m = await matrix(flow)
    assert cells(m, "SIGN")["result"] == {"value": "RMSE 1.316, Pearson R 0.797", "source": "user", "evidence": []}

    # Custom column, filled from each paper's passages (papers without full text stay empty)
    added = await flow.client.post(flow.url("/literature/comparison/columns"), json={"label": "Hardware"},
                                   headers=flow.headers)  # fmt: skip
    assert added.status_code == 201
    key = added.json()["columns"][-1]["key"]
    llm.responses.append(lambda prompt: {"answers": [
        {"paper": "P1", "value": "4× V100", "evidence": ["P1-C1"]},
        {"paper": "P2", "value": "Made up", "evidence": []},
    ]})  # fmt: skip
    assert (await run_job(flow, f"/literature/comparison/columns/{key}/fill"))["filled"] == 1
    m = await matrix(flow)
    filled = [r["cells"].get(key) for r in m["rows"]]
    assert sum(1 for c in filled if c) == 1 and next(c for c in filled if c)["evidence"]
    dup = await flow.client.post(flow.url("/literature/comparison/columns"), json={"label": "hardware"},
                                 headers=flow.headers)  # fmt: skip
    assert dup.status_code == 409

    # CSV: BOM, one row per selected paper, the researcher's value
    csv_resp = await flow.client.get(flow.url("/literature/comparison.csv"), headers=flow.headers)
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]
    table = list(csv.reader(io.StringIO(csv_resp.content.decode("utf-8-sig"))))
    assert table[0] == ["Paper", "Year", "Method", "Dataset", "Metric", "Result", "Limitation", "Hardware"]
    assert len(table) == 4 and "RMSE 1.316, Pearson R 0.797" in sum(table, [])

    # Deselecting a paper removes its row; built-in columns can't be deleted
    await flow.client.patch(flow.url(f"/literature/papers/{ids['closed']}"), json={"selected": False},
                            headers=flow.headers)  # fmt: skip
    assert len((await matrix(flow))["rows"]) == 2
    builtin = await flow.client.patch(flow.url("/literature/comparison/columns/method"), json={"remove": True},
                                      headers=flow.headers)  # fmt: skip
    assert builtin.status_code == 409
    removed = await flow.client.patch(flow.url(f"/literature/comparison/columns/{key}"), json={"remove": True},
                                      headers=flow.headers)  # fmt: skip
    assert [c["key"] for c in removed.json()["columns"]][-1] == "limitation"


async def test_insert_into_the_literature_survey(flow: Flow, llm: FakeLLM, db_session: AsyncSession) -> None:
    ids = await library(flow, db_session)
    body = {"caption": "Comparison of GNN affinity models."}
    blocked = await flow.post("/literature/comparison/insert", body)
    assert blocked.status_code == 409 and "run this stage first" in blocked.json()["detail"]

    await flow.run("literature")  # the stage's draft is now under review: its section is editable
    resp = await flow.post("/literature/comparison/insert", body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["table_number"] == 1

    section = (await flow.client.get(flow.url("/sections/literature_survey"), headers=flow.headers)).json()
    content = section["content"]
    assert "**Table 1.** Comparison of GNN affinity models." in content
    assert f"| SIGN [@{ids['sign']}] |" in content and "| Paper | Year | Method |" in content
    assert section["current_version"]["change_note"] == "Inserted comparison table (Table 1)"
    stored = await db_session.scalar(select(ManuscriptTable).where(ManuscriptTable.project_id == flow.pid))
    assert stored is not None and stored.number == 1 and len(stored.data["rows"]) == 3  # type: ignore[index]

    second = await flow.post("/literature/comparison/insert", {"caption": "Again.", "columns": ["method"]})
    assert second.json()["table_number"] == 2


async def test_running_a_later_stage_keeps_the_plans_domain(flow: Flow, llm: FakeLLM, db_session: AsyncSession) -> None:
    """Live bug: the project had no domain, and running Literature wiped the plan's domain (→ 500s)."""
    await library(flow, db_session)  # the flow's project has no domain; the plan says "Computational drug discovery"
    await flow.run("literature")
    resp = await flow.client.get(flow.url("/literature"), headers=flow.headers)
    assert resp.status_code == 200, resp.text
    plan = (await flow.client.get(flow.url("/idea"), headers=flow.headers)).json()["plan"]
    assert plan["domain"] == "Computational drug discovery"

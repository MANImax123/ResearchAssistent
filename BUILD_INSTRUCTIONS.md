# BUILD_INSTRUCTIONS.md — Multi-Agent, Human-in-the-Loop AI Research Paper Assistant

> **For the Antigravity agent:** This file is the single source of truth for this project.
> Read **Part A (Operating Rules)** and **Part B (Project Context)** fully before writing any code.
> Then execute **exactly one step** from **Part D (Build Plan)** at a time and stop for human approval.

Working name: **ResearchFlow** (rename freely; keep it consistent across the codebase).

---

# PART A — OPERATING RULES FOR THE AI AGENT (READ FIRST, FOLLOW ALWAYS)

## A1. Step-gated execution protocol

1. The build is divided into **Phases** and numbered **Steps** (e.g., `Step 2.3`).
2. Work on **one step only**. Never start the next step on your own.
3. At the start of every session, read `PROGRESS.md` (create it in Step 0.1) to find the current step.
4. For the current step:
   - Restate the step goal in 2–3 lines.
   - List the files you will create/modify.
   - Implement it fully (no TODO stubs unless the step explicitly says "stub").
   - Write the automated tests listed in the step.
   - Run the tests and the app where possible; fix failures before handing over.
5. When done, **STOP** and output a **Handover Report** in this exact format:

```
## Handover — Step X.Y: <title>
### What was built
- ...
### Files created/modified
- ...
### How to run
<exact commands>
### How to test (manual)
1. ...
2. ...
### Automated tests
<command> → <result summary>
### Known limitations / decisions made
- ...
### Waiting for approval
Reply "APPROVED X.Y" to continue, or describe changes needed.
```

6. Only when the human replies **`APPROVED X.Y`**:
   - Mark the step `✅` in `PROGRESS.md` with the date.
   - Commit with message `step X.Y: <title>`.
   - Then (and only then) start Step X.Y+1.
7. If the human requests changes, apply them **within the same step**, re-run tests, and give a new Handover Report.
8. If a step is ambiguous or blocked (missing API key, unclear requirement), **ask before guessing**. Do not silently change the tech stack.
9. Never break previously approved features. Before handing over, re-run the full test suite (`make test`).

## A2. PROGRESS.md format

```
# Progress
Current step: X.Y
| Step | Title | Status | Approved on | Notes |
|------|-------|--------|-------------|-------|
| 0.1  | Repo & infra scaffolding | ✅ | 2026-10-21 | |
| 0.2  | ... | 🔄 in progress | | |
```

Also keep a `DECISIONS.md` log for any design decision not stated in this file.

## A3. Engineering rules

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x (async), Alembic, Pydantic v2, type hints everywhere.
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind CSS.
- **Never hardcode secrets.** All config via `.env` (provide `.env.example`, keep `.env` in `.gitignore`).
- **All LLM calls go through one service** (`backend/app/services/llm.py`). No agent calls the OpenAI SDK directly. This lets us swap models/providers later.
- **All LLM outputs that feed the system must be structured** (Pydantic schemas / JSON mode / function calling). Free text only for prose sections.
- **All prompts live in** `backend/app/agents/prompts/` as versioned Python templates — not inline in agent logic.
- **Long-running work (search, parsing, embeddings, generation) runs in Celery**, never blocking an HTTP request. API returns a `job_id`; frontend polls/streams status.
- **Every agent run is logged** to the `agent_runs` table (input summary, output, model, tokens, latency, status, error).
- **Tests:** `pytest` for backend (mock LLM and external APIs in unit tests), basic component/e2e checks for frontend where specified.
- Keep functions small; one agent = one module.

## A4. Non-negotiable product rules (integrity)

1. **No fabricated citations.** The system may only cite papers that exist in the project's paper library with a verified identifier (DOI / Semantic Scholar ID / arXiv ID / OpenAlex ID). Generated text uses citation keys like `[@paper_id]` that are resolved by the References Agent. Any unresolved key is flagged, never invented.
2. **Grounded claims.** Literature-derived claims must carry the source passage IDs they were generated from (RAG evidence).
3. **Research gaps are suggestions, not facts.** The Gap Detection Agent outputs *candidate* gaps with supporting evidence and a confidence level. UI wording: "Potential gap", never "Gap confirmed".
4. **Human approval gates.** No stage's output becomes "final" without explicit researcher approval. The researcher can edit, reject, regenerate, or go back to an earlier stage.
5. **Researcher-provided content wins.** User-supplied methodology, results, and data are never altered in meaning; the AI only structures and phrases them. Numbers in Results must come from user data, never generated.

---

# PART B — PROJECT CONTEXT

## B1. One-paragraph summary

An end-to-end, multi-agent, AI-powered research assistant that supports researchers through the **complete research-paper lifecycle**: idea & problem identification → abstract → introduction → literature survey → methodology → results & discussion → conclusion → references → manuscript review → journal/conference formatting. Specialized agents perform stage-specific work while reading from and writing to a **shared research state**, so the paper evolves as **one unified document**, not disconnected sections. A **human-in-the-loop** design keeps the researcher in control: they provide inputs, upload material, review, edit, and approve each stage before the next begins. The final manuscript is exported in a target venue's format (IEEE, Springer, ACM, Elsevier, etc.) as DOCX, LaTeX, or PDF.

## B2. Problem being solved

Researchers juggle fragmented tools (Google Scholar, Semantic Scholar, ResearchRabbit for search; Elicit/Consensus for literature; ChatGPT/Claude for summarizing; Zotero for citations; Word/LaTeX for writing). Existing AI research systems each solve only part of the problem:

| Existing system | What it does | Gap we address |
|---|---|---|
| The AI Scientist (Lu et al.) | Fully autonomous ideation→code→paper→review | Hallucinations, low-quality output without human oversight |
| PaperOrchestrator | 7-stage LLM pipeline w/ shared context memory | Citation hallucination, latency, CS-centric |
| PaperOrchestra | Turns unstructured notes/data into drafts | Variable review depth |
| Agent Laboratory | Human-guided multi-agent lab (PhD/Postdoc roles) | Fragile experiment execution |
| Prompt-to-Paper | Grounded, deterministic, citation-verified writing | Bioinformatics-specific |
| LitLLM, LiRA, OpenScholar, Hybrid-RAG lit review | Literature review generation only | Not full lifecycle |
| SciSpace Copilot | Paper reading assistant | No idea→manuscript pipeline |
| ResearchAgent, AI Co-Scientist, VIRSCI, MOOSE-Chem | Idea/hypothesis generation | No manuscript production/formatting |
| PaperEval | Evaluates finished papers | Doesn't guide drafting |
| HITL writing framework, Scholarly HI–AI governance | Human-AI writing process/governance | Conceptual, not a working system |

**Common gaps:** fragmented workflow, poor context continuity across sections, hallucinated citations, limited human control, domain-specific designs, weak research-gap identification, limited publication-format support.

## B3. Insights adopted from prior work

1. **Shared research context** (PaperOrchestrator) → central `ResearchState` read by all agents.
2. **Human-in-the-loop** (Agent Laboratory) → approval checkpoint after every stage.
3. **Grounded generation & citation verification** (Prompt-to-Paper, LitLLM, PaperQA) → RAG evidence + verified references only.
4. **Research gap identification** (SciMON, ResearchAgent) → compare limitations/contradictions across papers to propose candidate gaps.
5. **Multi-agent criticism** (AI Co-Scientist) → novelty / feasibility / methodology critic passes on the research idea.
6. **Unstructured material processing** (PaperOrchestra) → accept notes, PDFs, tables, figures, raw results.
7. **End-to-end pipeline** (AI Scientist, AI-Researcher) → connected stages, but human-approved, not autonomous.
8. **Quality evaluation** (ScienceAgentBench, PaperEval) → pre-export consistency & completeness checks.

## B4. Scope

**AI-assisted, human-controlled research paper development.**

| Researcher does | AI does |
|---|---|
| Gives the research idea | Analyzes |
| Reviews AI suggestions | Retrieves |
| Provides information & uploads papers | Summarizes |
| Provides methodology, experiment details, results | Compares |
| Modifies generated content | Generates drafts |
| Approves each stage | Organizes, maintains context, formats the final manuscript |

**Out of scope (v1):** autonomous code/experiment execution, automatic submission to venues, plagiarism detection, real-time multi-user co-editing.

## B5. Objectives

1. Assist researchers in research planning and problem identification.
2. Automate/assist literature discovery and analysis.
3. Extract and compare methodologies, datasets, results, and limitations.
4. Assist in identifying potential research gaps.
5. Generate and refine individual paper sections using accumulated project context.
6. Support multiple users, projects, and sessions.
7. Let users review, modify, and approve AI outputs at every stage.
8. Generate a structured manuscript per journal/conference requirements.

## B6. Workflow (stage order)

```
[1] Idea & Problem ──► [2] Literature Survey ──► (refine objectives/RQs) ──► [3] Methodology
      ▲                                                                           │
      │                    (researcher can jump back to any earlier stage)        ▼
[8] Formatting & Export ◄── [7] Review ◄── [6] References ◄── [5] Conclusion ◄── [4] Results & Discussion
                                     (Introduction + final Abstract are generated after Results,
                                      using the full approved context)
```

Each stage: `input → agent run(s) → draft → HUMAN REVIEW (approve / edit / reject+feedback / regenerate / go back) → state update → next stage`.

## B7. Architecture

```
Researcher
   │
Next.js Web App (dashboard, chat, uploads, stage tracker, section editor, paper library, gap view, KG view, export)
   │  REST + SSE
FastAPI Backend ── Auth (JWT)
   │
Agent Orchestrator (LangGraph StateGraph + Postgres checkpointer + interrupts for approvals)
   │
   ├── Stage agents: Idea/Problem · Literature Survey (sub-agent pipeline) · Methodology
   │                 · Results & Discussion · Introduction · Conclusion & Synthesis · References
   │                 · Review/Consistency · Formatting
   │
Shared tools/services: LLM service · Embeddings · Academic APIs (Semantic Scholar, OpenAlex,
   Crossref, arXiv, Unpaywall, Zotero) · PDF processing (PyMuPDF, GROBID) · Data analysis
   (Pandas, NumPy, SciPy, scikit-learn) · Charts (Matplotlib/Plotly) · Templates (LaTeX, python-docx)
   │
Data layer: PostgreSQL (structured) · pgvector (embeddings) · Neo4j (knowledge graph)
           · MinIO (files) · Redis (Celery broker/cache)
```

## B8. Agents (stage agents + literature sub-agents)

### Stage agents
| # | Agent | Responsibilities | Main tech |
|---|---|---|---|
| 1 | **Research Idea & Problem Development** | Refine idea; problem statement; motivation; objectives; research questions/hypotheses; scope; expected contributions; research directions; title; draft & final abstract | LLM, ResearchState, KG |
| 1a | Critic sub-agents (Novelty, Feasibility, Methodology) | Independently critique the proposed idea; return scored feedback | LLM |
| 2 | **Literature Survey** (orchestrates sub-agents below) | Search, retrieve, extract, summarize, compare, find trends & candidate gaps, validate/refine objectives & RQs, write literature survey | S2, OpenAlex, Crossref, arXiv, GROBID, RAG, pgvector, Neo4j |
| 3 | **Methodology** | Turn researcher's technical input into structured methodology: architecture, algorithms, workflow, implementation, datasets, preprocessing, experimental setup, tools/params, diagrams, metrics, reproducibility | LLM (+vision for diagrams), doc processing |
| 4 | **Results & Discussion** | Process user results (CSV/XLSX/logs); compute/interpret metrics; tables & charts; stats tests; compare with literature; strengths/limitations | Pandas, NumPy, SciPy, scikit-learn, Matplotlib/Plotly, LLM |
| 5 | **Introduction** | Write intro from approved problem, motivation, literature context, contributions | LLM, ResearchState |
| 6 | **Conclusion & Synthesis** | Link objectives↔RQs↔methods↔results; verify every RQ is answered; contributions; limitations; future work; coherence | LLM, KG, ResearchState |
| 7 | **References** | Verify metadata; resolve citation keys; bibliography; citation styles; missing/duplicate detection; in-text ↔ list consistency; Zotero import | Crossref, S2, Zotero, citeproc |
| 8 | **Review / Consistency** | Pre-export checks: section completeness, objective↔method↔result↔conclusion consistency, citation integrity, numeric consistency | LLM, rules |
| 9 | **Formatting** | Apply venue template; headings, fonts, margins, figures/tables/captions/numbering; citation format; export PDF/DOCX/LaTeX | LaTeX, python-docx, templates, LibreOffice |

### Literature Survey sub-agents (pipeline inside stage 2)
| # | Sub-agent | Role |
|---|---|---|
| L1 | Research Planning | Break topic into subtopics, research questions, search tasks/queries |
| L2 | Paper Discovery | Query Semantic Scholar, OpenAlex, arXiv, Crossref; deduplicate |
| L3 | Relevance Ranking | Rank by relevance to RQs (embedding similarity + LLM rerank) |
| L4 | Document Parsing | GROBID/PyMuPDF → abstract, methodology, dataset, results, limitations |
| L5 | RAG Retrieval | Retrieve relevant passages from processed papers (pgvector) |
| L6 | Paper Analysis | Structured paper profile (problem, method, dataset, metrics, results, limitations, tech) |
| L7 | Comparative Analysis | Comparison matrix: Paper · Method · Dataset · Metric · Result · Limitation |
| L8 | Contradiction Detection | Find conflicting claims/results across papers, with evidence |
| L9 | Knowledge Graph | Build relationships in Neo4j (Paper→USES→Method, Paper→EVALUATED_ON→Dataset, …) |
| L10 | Research Gap Detection | Propose **candidate** gaps from limitations, contradictions, under-explored areas, with evidence + confidence |
| L11 | Report Generation | Compile literature analysis report + Literature Survey section |

## B9. Tech stack (fixed — do not change without asking)

| Layer | Technology |
|---|---|
| Frontend | Next.js (React, App Router, TypeScript) |
| UI | Tailwind CSS (+ a rich-text editor such as TipTap for sections) |
| Backend | Python + FastAPI |
| Orchestration | LangGraph (StateGraph, Postgres checkpointer, interrupts) |
| LLM | OpenAI API (GPT model, configurable via env) |
| Prompts | Python prompt templates |
| Embeddings | OpenAI embeddings (default) / BGE (optional local) |
| RAG | LangChain/LangGraph + custom retrieval |
| Vector DB | PostgreSQL + pgvector |
| Structured DB | PostgreSQL |
| Knowledge Graph | Neo4j |
| PDF processing | PyMuPDF + GROBID |
| Doc generation | python-docx + LaTeX |
| PDF generation | LaTeX (latexmk/pdflatex) / LibreOffice conversion |
| Data analysis | Pandas + NumPy |
| Statistics | SciPy + scikit-learn |
| Visualization | Matplotlib / Plotly |
| References | Zotero API / Crossref |
| Academic search | Semantic Scholar + OpenAlex + Crossref (+ arXiv, Unpaywall, ORCID) |
| Task queue | Celery + Redis |
| File storage | MinIO (S3-compatible) |
| Auth | JWT (OAuth 2.0 later) |
| Containers | Docker + Docker Compose |
| VCS | Git + GitHub |

### Academic API notes
- **Semantic Scholar Graph API:** paper search, metadata, abstracts, citations/references. API key optional (`S2_API_KEY`); respect rate limits with retry/backoff.
- **OpenAlex:** works, authors, topics, sources. Send `mailto` param (`OPENALEX_EMAIL`) for the polite pool.
- **Crossref REST:** DOI metadata; include `mailto` in User-Agent (`CROSSREF_EMAIL`).
- **arXiv API:** Atom feed search; open PDFs.
- **Unpaywall:** legal open-access PDF location by DOI; requires `email` param.
- **Zotero Web API:** user's library import (user provides API key + user/group ID).
- **ORCID:** optional author metadata.
- All API clients: async `httpx`, timeouts, retries with exponential backoff, response caching in Redis (24h), and normalization into one internal `PaperMetadata` schema.

## B10. Repository structure (target)

```
researchflow/
├── BUILD_INSTRUCTIONS.md      # this file
├── PROGRESS.md
├── DECISIONS.md
├── README.md
├── docker-compose.yml
├── .env.example
├── Makefile                   # up, down, migrate, test, lint, worker, seed
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/              # config, security, logging, deps
│   │   ├── db/                # session, base, models/
│   │   ├── schemas/           # Pydantic API + agent output schemas
│   │   ├── api/v1/            # routers: auth, projects, files, stages, sections, papers, gaps, kg, references, export, jobs
│   │   ├── services/          # llm.py, embeddings.py, storage.py, grobid.py, pdf.py, academic/ (s2, openalex, crossref, arxiv, unpaywall, zotero), neo4j.py, citations.py
│   │   ├── agents/
│   │   │   ├── prompts/
│   │   │   ├── idea/
│   │   │   ├── literature/    # planning, discovery, ranking, parsing, rag, analysis, comparison, contradiction, kg, gaps, report
│   │   │   ├── methodology/
│   │   │   ├── results/
│   │   │   ├── introduction/
│   │   │   ├── conclusion/
│   │   │   ├── references/
│   │   │   ├── review/
│   │   │   └── formatting/
│   │   ├── orchestrator/      # state.py, graph.py, checkpoints.py, router.py
│   │   └── workers/           # celery_app.py, tasks.py
│   ├── templates/             # venue templates: ieee/, springer_lncs/, acm/, elsevier/
│   └── tests/
└── frontend/
    ├── Dockerfile
    ├── app/                   # (auth), dashboard, projects/[id]/{overview,idea,literature,methodology,results,discussion,conclusion,references,review,export}
    ├── components/
    ├── lib/                   # api client, auth, hooks
    └── tests/
```

## B11. Core data model (PostgreSQL)

| Table | Key fields |
|---|---|
| `users` | id, email, password_hash, name, created_at |
| `projects` | id, owner_id, title, domain, target_venue, citation_style, current_stage, status, created_at |
| `project_members` | project_id, user_id, role (owner/editor/viewer) |
| `research_state` | project_id, JSONB snapshot of `ResearchState`, version |
| `stages` | id, project_id, name, status (`not_started`/`running`/`awaiting_review`/`approved`/`rejected`/`stale`), approved_by, approved_at |
| `sections` | id, project_id, type (title/abstract/introduction/literature_survey/methodology/results/discussion/conclusion/references), current_version_id, status |
| `section_versions` | id, section_id, content (markdown/JSON), source (`ai`/`user`/`ai_edited`), agent_run_id, created_by, created_at, change_note |
| `files` | id, project_id, kind (paper_pdf/dataset/diagram/code/notes/result), object_key, mime, size, uploaded_by |
| `papers` | id, project_id, external ids (doi, s2_id, arxiv_id, openalex_id), title, authors JSONB, year, venue, abstract, url, pdf_file_id, relevance_score, selected (bool), parse_status |
| `paper_profiles` | paper_id, problem, method, dataset, metrics, results, limitations, tech_stack, evidence JSONB |
| `paper_chunks` | id, paper_id, section_name, text, embedding vector(1536), page |
| `comparisons` | project_id, matrix JSONB, version |
| `contradictions` | id, project_id, claim_a, claim_b, paper_a_id, paper_b_id, evidence JSONB, explanation |
| `research_gaps` | id, project_id, statement, rationale, evidence JSONB, confidence, status (candidate/accepted/rejected) |
| `references` | id, project_id, paper_id (nullable), citation_key, csl_json, verified (bool), source |
| `figures` / `tables` | id, project_id, section_type, file_id or data JSONB, caption, number |
| `agent_runs` | id, project_id, agent, stage, input_summary, output JSONB, model, tokens_in, tokens_out, latency_ms, status, error |
| `feedback` | id, project_id, stage, section_id, user_id, action (approve/edit/reject/regenerate/back), comment |
| `jobs` | id, project_id, celery_task_id, type, status, progress, result_ref, error |

## B12. ResearchState (shared context, LangGraph state)

```python
class ResearchState(TypedDict):
    project_id: str
    current_stage: str
    idea: IdeaBlock            # raw_idea, refined_idea, domain, keywords
    problem: ProblemBlock      # statement, motivation, scope, contributions
    objectives: list[Objective]
    research_questions: list[ResearchQuestion]   # id, text, linked_objective_ids
    title: str | None
    abstract_draft: str | None
    selected_paper_ids: list[str]
    comparison_matrix_id: str | None
    gap_ids: list[str]                 # accepted gaps only
    methodology: MethodologyBlock      # architecture, algorithms, datasets, setup, metrics, diagrams
    results: ResultsBlock              # metrics, tables, figures, stats, user notes
    section_ids: dict[str, str]        # section_type -> section_id
    reference_keys: list[str]
    venue: VenueConfig                 # name, template, citation_style, page_limit
    approvals: dict[str, ApprovalRecord]
    pending_feedback: str | None
    stale_stages: list[str]            # stages invalidated by upstream changes
```

**Staleness rule:** if an approved upstream item changes (e.g., objectives edited after literature approval), downstream stages that depend on it are marked `stale` and the UI prompts the researcher to review/regenerate them. Changes in results must flag abstract, introduction, discussion and conclusion as stale.

## B13. Knowledge graph schema (Neo4j)

Nodes: `Project, Paper, Method, Dataset, Metric, Limitation, Gap, Objective, ResearchQuestion, Experiment, Result, Conclusion`
Relationships:
`(Paper)-[:USES]->(Method)`, `(Paper)-[:EVALUATED_ON]->(Dataset)`, `(Paper)-[:REPORTS {value}]->(Metric)`, `(Paper)-[:HAS_LIMITATION]->(Limitation)`, `(Paper)-[:CONTRADICTS {evidence}]->(Paper)`, `(Gap)-[:DERIVED_FROM]->(Limitation|Paper)`, `(Objective)-[:ADDRESSES]->(Gap)`, `(ResearchQuestion)-[:SUPPORTS]->(Objective)`, `(Method)-[:ANSWERS]->(ResearchQuestion)`, `(Experiment)-[:EVALUATES]->(Method)`, `(Result)-[:FROM]->(Experiment)`, `(Conclusion)-[:BASED_ON]->(Result)`
All nodes carry `project_id`; every query filters by it.

---

# PART C — ENVIRONMENT

`.env.example` must include at least:
```
# Core
APP_ENV=dev
SECRET_KEY=
JWT_EXPIRE_MINUTES=1440
# Postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=researchflow
POSTGRES_USER=researchflow
POSTGRES_PASSWORD=
# Redis
REDIS_URL=redis://redis:6379/0
# MinIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET=researchflow
# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
# GROBID
GROBID_URL=http://grobid:8070
# LLM
OPENAI_API_KEY=
LLM_MODEL=           # main reasoning/generation model
LLM_MODEL_FAST=      # cheaper model for extraction/classification
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
# Academic APIs
S2_API_KEY=
OPENALEX_EMAIL=
CROSSREF_EMAIL=
UNPAYWALL_EMAIL=
# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

Docker Compose services: `postgres` (pgvector image), `redis`, `minio`, `neo4j`, `grobid`, `backend`, `worker` (Celery), `frontend`.

---

# PART D — BUILD PLAN (one step at a time, approval required after each)

Each step lists **Goal · Tasks · Tests · Acceptance (what the human verifies)**.

---

## PHASE 0 — Foundation

### Step 0.1 — Repository & infrastructure scaffolding
**Goal:** Monorepo skeleton with all infrastructure services running.
**Tasks:**
- Create repo structure from B10 (empty packages with `__init__.py` are fine).
- `docker-compose.yml` with postgres (pgvector), redis, minio (+ bucket auto-create), neo4j, grobid; healthchecks on each.
- `.env.example`, `.gitignore`, `Makefile` (`up`, `down`, `logs`, `ps`), `README.md` quickstart.
- Create `PROGRESS.md` (all steps from Part D listed, 0.1 in progress) and `DECISIONS.md`.
**Tests:** script `scripts/check_infra.py` that pings each service and prints OK/FAIL.
**Acceptance:** `make up` → all containers healthy; `python scripts/check_infra.py` prints OK for Postgres, pgvector extension, Redis, MinIO bucket, Neo4j, GROBID; MinIO console and Neo4j browser open in the browser.

### Step 0.2 — FastAPI backend skeleton
**Goal:** Running API with config, logging, and health endpoint.
**Tasks:** `app/main.py`, `core/config.py` (pydantic-settings), structured logging, CORS, `/api/v1/health` returning status of DB/Redis/MinIO/Neo4j/GROBID; backend Dockerfile; add to compose.
**Tests:** pytest for `/health` (dependencies mocked).
**Acceptance:** `http://localhost:8000/docs` loads; `/api/v1/health` shows every dependency "ok".

### Step 0.3 — Database models & migrations
**Goal:** All B11 tables created via Alembic.
**Tasks:** async SQLAlchemy models for all tables, pgvector column on `paper_chunks`, Alembic init + first migration, `make migrate`, seed script with one demo user/project.
**Tests:** migration up/down test; model CRUD smoke test against a test DB.
**Acceptance:** `make migrate` succeeds; tables visible in DB (`\dt`); seed runs.

### Step 0.4 — Authentication (JWT)
**Goal:** Register, login, current-user endpoints.
**Tasks:** bcrypt password hashing, JWT access tokens, `get_current_user` dependency, `/auth/register`, `/auth/login`, `/auth/me`.
**Tests:** register → login → me; wrong password → 401; protected route without token → 401.
**Acceptance:** Flow works from Swagger UI.

### Step 0.5 — Frontend skeleton + auth
**Goal:** Next.js app with login/register and protected dashboard shell.
**Tasks:** Next.js + TS + Tailwind; API client with token handling; login/register pages; protected layout with sidebar; logout; frontend Dockerfile + compose service.
**Tests:** basic render test for login page.
**Acceptance:** Register → land on empty dashboard → logout → login again works; refresh keeps session.

---

## PHASE 1 — Projects, files, jobs, LLM service

### Step 1.1 — Projects CRUD + dashboard
**Goal:** Users create/manage multiple research projects (Objective 6).
**Tasks:** API `/projects` (create, list, get, update, delete, ownership checks, members table support); dashboard cards (title, domain, current stage, last updated); "New Project" form (title, domain, raw idea, target venue optional); project overview page with **stage tracker** (all 9 stages shown, status `not_started`).
**Tests:** CRUD; user A cannot see user B's project.
**Acceptance:** Create 2 projects, edit one, delete one; second user account sees none of them.

### Step 1.2 — File uploads (MinIO)
**Goal:** Upload/list/download/delete project files.
**Tasks:** `services/storage.py`; `/projects/{id}/files` endpoints; allowed kinds from B11; size/type validation; presigned download URLs; frontend upload panel (drag & drop, kind selector, list).
**Tests:** upload/download roundtrip; invalid type rejected.
**Acceptance:** Upload a PDF, a CSV, and a PNG; download each; delete one.

### Step 1.3 — Celery background jobs + job status
**Goal:** Infrastructure for long-running tasks.
**Tasks:** `workers/celery_app.py`, worker service in compose, `jobs` table updates, `/jobs/{id}` endpoint, SSE or polling endpoint for progress; a demo task (`sleep` with progress updates); frontend `useJob` hook + progress bar component.
**Tests:** task enqueued → status transitions pending→running→success (eager mode in tests).
**Acceptance:** Trigger demo job from UI, see progress bar complete.

### Step 1.4 — LLM & embeddings service
**Goal:** Single, testable gateway for all model calls.
**Tasks:** `services/llm.py` with `generate_text()`, `generate_structured(schema: type[BaseModel])`, retries, timeouts, token/latency capture, writes to `agent_runs`; `services/embeddings.py` with batching; a `FakeLLM` for tests; prompt template base class in `agents/prompts/base.py`; admin/debug endpoint `/debug/llm-test` (dev only).
**Tests:** structured output parses into Pydantic; FakeLLM used in unit tests; agent_runs row written.
**Acceptance:** Calling the debug endpoint returns a valid structured object and an `agent_runs` row appears with token counts.

---

## PHASE 2 — Orchestrator & Human-in-the-loop core

### Step 2.1 — ResearchState + LangGraph orchestrator skeleton
**Goal:** Stateful graph of all stages using **stub agents**.
**Tasks:** `orchestrator/state.py` (B12, Pydantic/TypedDict); `graph.py` StateGraph with nodes for all 9 stages (stubs return placeholder drafts); LangGraph Postgres checkpointer keyed by `project_id` as thread; `interrupt` after each stage node for human review; persistence of state snapshot to `research_state`; API: `POST /projects/{id}/stages/{stage}/run`, `GET /projects/{id}/state`.
**Tests:** run stage 1 stub → graph pauses at review interrupt → state persisted → resume continues.
**Acceptance:** From Swagger, run stage 1 → state shows `awaiting_review`; restart backend container → state still there.

### Step 2.2 — Approval checkpoints & feedback actions
**Goal:** Researcher controls progression.
**Tasks:** endpoints for `approve`, `reject` (with comment), `regenerate` (with feedback injected as `pending_feedback`), `edit` (user content becomes a new version), `go_back` (to earlier stage); write `feedback` rows; implement **staleness rule** (B12); gate: a stage cannot run until its prerequisites are approved (configurable map).
**Frontend:** stage tracker reflects statuses with colors; Review panel with Approve / Edit / Reject + comment / Regenerate / Go back buttons.
**Tests:** prerequisite gating; regenerate passes feedback; go_back marks downstream stale.
**Acceptance:** Using stubs: approve stage 1, run 2, go back to 1, edit, see stage 2 marked stale.

### Step 2.3 — Section editor with versioning
**Goal:** Every paper section is editable with history.
**Tasks:** section/version APIs (list versions, get, create, restore, diff); TipTap (or similar) rich-text editor supporting headings, lists, tables, inline citation chips `[@key]`, figure placeholders; autosave as user version; version history drawer with restore; "AI draft" vs "edited" badges.
**Tests:** version create/restore; diff endpoint.
**Acceptance:** Edit a stub section, autosave, restore an older version.

---

## PHASE 3 — Stage 1: Research Idea & Problem Development

### Step 3.1 — Research Planning + idea refinement
**Goal:** Turn raw idea into structured plan.
**Tasks:** Idea agent produces (structured): refined idea, domain, keywords, subtopics, problem statement, motivation, scope, expected contributions, 3–6 objectives, research questions/hypotheses linked to objectives, suggested research directions, 3 title options. Researcher can attach notes/files as extra context.
**Frontend:** Idea workspace page: input box + uploads → generate → editable cards for each block; pick/edit title.
**Tests:** schema validation; RQs always link to existing objectives.
**Acceptance:** Enter a real idea (e.g., this project itself) → get a coherent plan → edit an objective → approve.

### Step 3.2 — Critic agents (Novelty, Feasibility, Methodology)
**Goal:** Multi-agent critique of the idea before literature work.
**Tasks:** three critic agents run in parallel (LangGraph fan-out), each returns score (1–5), strengths, risks, suggestions; idea agent produces a revised version incorporating accepted suggestions; UI shows critiques side-by-side; researcher chooses which suggestions to apply.
**Tests:** parallel execution; revision only applies selected suggestions.
**Acceptance:** Critiques are specific (not generic); applying 2 of 5 suggestions changes only relevant fields.

### Step 3.3 — Draft abstract
**Goal:** Early working abstract from approved idea (finalized later in Step 7.2).
**Tasks:** abstract agent with length control (target word count from venue config or default 200–250); saved as `abstract` section version.
**Acceptance:** Abstract reflects approved objectives; editable; stage 1 can be approved as a whole.

---

## PHASE 4 — Stage 2: Literature Survey pipeline

### Step 4.1 — Academic API clients + Paper Discovery agent (L1, L2)
**Goal:** Find candidate papers from multiple sources.
**Tasks:** async clients for Semantic Scholar, OpenAlex, arXiv, Crossref (normalize to `PaperMetadata`); Research Planning sub-agent generates search queries per subtopic/RQ; Discovery runs queries in a Celery job, deduplicates (DOI → title fuzzy match), stores in `papers`; year/venue filters; manual "add by DOI/arXiv ID" and "upload PDF" options.
**Frontend:** Literature → "Discover" tab: queries (editable), run, results table with source badges.
**Tests:** client normalization with recorded fixtures; dedup logic.
**Acceptance:** 30–100 deduplicated, real papers for a test topic; manual DOI add works.

### Step 4.2 — Relevance Ranking agent (L3) + selection
**Goal:** Rank and let the researcher select papers.
**Tasks:** embed title+abstract; cosine similarity to RQs; LLM rerank top-N with short "why relevant" justification; store `relevance_score`; UI to sort/filter, select/deselect, and mark "must include".
**Acceptance:** Top results are clearly on-topic; selection persists; selected set becomes `selected_paper_ids`.

### Step 4.3 — PDF acquisition + Document Parsing agent (L4)
**Goal:** Get full text and structure for selected papers.
**Tasks:** fetch open-access PDFs (arXiv, Unpaywall, S2 openAccessPdf) — legal sources only; otherwise prompt user to upload; GROBID `processFulltextDocument` → TEI → sections; PyMuPDF fallback; extract abstract, methodology, dataset, results, limitations, figure/table captions; chunk by section (~500–800 tokens, overlap); `parse_status` per paper; Celery job with per-paper progress.
**Acceptance:** For selected papers, parse status visible; open a paper → see extracted sections; papers without OA PDF clearly marked "upload needed".

### Step 4.4 — Embeddings + RAG Retrieval agent (L5)
**Goal:** Semantic search over parsed papers.
**Tasks:** embed chunks into `paper_chunks` (pgvector, HNSW index); retrieval function with project filter, top-k, optional section filter, MMR; "Ask the literature" chat in UI returning answers with **chunk-level citations** (paper + section + page).
**Tests:** retrieval returns only same-project chunks; answer cites only retrieved chunks.
**Acceptance:** Ask "Which datasets are used for X?" → grounded answer with clickable sources.

### Step 4.5 — Paper Analysis agent (L6)
**Goal:** Structured profile per paper.
**Tasks:** profile fields from B11 `paper_profiles`, each field with evidence chunk IDs; editable by researcher.
**Acceptance:** Profiles are accurate for 3 papers the researcher knows well; evidence links open the right passage.

### Step 4.6 — Comparative Analysis agent (L7)
**Goal:** Comparison matrix.
**Tasks:** build table Paper · Year · Method · Dataset · Metric · Result · Limitation (+ user-addable columns); sortable/filterable UI; export CSV; can be inserted into the literature section as a table.
**Acceptance:** Matrix covers all selected papers; edits persist; CSV export opens correctly.

### Step 4.7 — Contradiction Detection agent (L8)
**Goal:** Surface conflicting claims.
**Tasks:** group profiles by shared method/dataset/metric; LLM compares claims pairwise within groups; output claim A vs claim B, papers, evidence, possible explanation (different setup, dataset version, metric definition). Researcher can dismiss.
**Acceptance:** Contradictions shown with both evidence passages; dismissals persist.

### Step 4.8 — Knowledge Graph agent (L9) + visualization
**Goal:** Neo4j graph of literature + project entities (B13).
**Tasks:** `services/neo4j.py`; write nodes/edges from profiles, contradictions, objectives, RQs; entity normalization (merge "BERT" / "bert-base"); `/projects/{id}/kg` endpoint returning a subgraph; frontend graph view (e.g., react-force-graph or Cytoscape) with filters by node type.
**Acceptance:** Graph shows papers linked to methods/datasets; clicking a node shows details; data isolated per project.

### Step 4.9 — Research Gap Detection agent (L10)
**Goal:** Candidate gaps with evidence.
**Tasks:** inputs = limitations, contradictions, under-explored method×dataset combinations (from KG), future-work statements; output 3–8 candidate gaps, each with rationale, evidence (papers/chunks), confidence (low/med/high), and suggested link to objectives. UI label "Potential gap". Researcher accepts/rejects/edits; accepted gaps stored in state and KG (`Objective-[:ADDRESSES]->Gap`).
**Acceptance:** Gaps are specific and traceable; none phrased as certain facts.

### Step 4.10 — Objective/RQ refinement + Literature Survey section (L11)
**Goal:** Close the loop and write the section.
**Tasks:** agent proposes objective/RQ refinements based on accepted gaps (diff view; researcher accepts per item → triggers staleness rule if stage 1 changes); Report Generation agent writes (a) a literature analysis report (downloadable) and (b) the Literature Survey section organized thematically (not paper-by-paper), every claim with `[@paper_id]` keys, ending with gap summary; optional comparison table inserted.
**Tests:** every citation key in the section exists in `papers` with a verified ID.
**Acceptance:** Section reads like a real related-work section; all citations resolve; approve stage 2.

---

## PHASE 5 — Stage 3: Methodology

### Step 5.1 — Methodology input workspace
**Goal:** Collect researcher-provided technical details.
**Tasks:** structured form + free text: system architecture, algorithms, workflow, datasets (name, source, size, features), preprocessing, experimental setup, hyperparameters, tools/frameworks/libraries, evaluation metrics, reproducibility info; uploads for diagrams/code/notes; agent asks **follow-up questions** for missing essentials (e.g., no metrics given).
**Acceptance:** Agent's follow-up questions are relevant to what's actually missing.

### Step 5.2 — Diagram understanding
**Goal:** Use uploaded architecture diagrams/flowcharts.
**Tasks:** vision-capable model describes uploaded diagrams (components, flow); researcher confirms/edits the description; diagrams registered as figures with auto-numbering and editable captions.
**Acceptance:** Description of a real architecture diagram is accurate after minor edits; figure appears with caption.

### Step 5.3 — Methodology section generation
**Goal:** Full methodology section.
**Tasks:** generate subsections (Overview, Architecture, Dataset, Preprocessing, Algorithms/Model, Experimental Setup, Evaluation Metrics); insert figure references; may cite literature for borrowed methods (verified keys only); consistency check that each RQ maps to a method component (show unmapped RQs as warnings).
**Acceptance:** Section is faithful to inputs (no invented hyperparameters); approve stage 3.

---

## PHASE 6 — Stage 4: Results & Discussion

### Step 6.1 — Results data ingestion & analysis
**Goal:** Process user experimental data.
**Tasks:** upload CSV/XLSX/JSON logs; preview + column mapping (model/config, metric columns); compute summary stats with Pandas/NumPy; metrics via scikit-learn when raw predictions are provided (accuracy, precision, recall, F1, ROC-AUC, RMSE, etc.); statistical tests via SciPy when applicable (t-test, Wilcoxon, ANOVA) with assumptions stated. **All numbers computed by code, never by the LLM.**
**Tests:** metric computations against known values.
**Acceptance:** Upload a sample results CSV → correct summary table and test results.

### Step 6.2 — Tables & charts
**Goal:** Publication-quality visuals.
**Tasks:** chart builder (bar, line, box, confusion matrix, ROC) with Matplotlib (static, for export, 300 dpi PNG/PDF) and Plotly (interactive preview); auto-numbered tables/figures with captions; user picks which go into the paper.
**Acceptance:** Generate 2 charts + 1 table; captions editable; numbering correct.

### Step 6.3 — Results & Discussion section
**Goal:** Interpret results in context.
**Tasks:** Results subsection = factual reporting referencing tables/figures (numbers pulled from computed data); Discussion = interpretation, comparison with literature from comparison matrix (cited), strengths, limitations, answers to each RQ (explicitly mapped). Numeric consistency check: every number in text must match computed data (flag mismatches).
**Acceptance:** No number in the text disagrees with the data; each RQ addressed; approve stage 4.

---

## PHASE 7 — Introduction, final Abstract, Conclusion

### Step 7.1 — Introduction agent
**Goal:** Introduction from full approved context.
**Tasks:** structure: context/background → problem → limitations of existing work (cited) → gap → objectives/contributions (bulleted) → paper organization paragraph.
**Acceptance:** Contributions match approved objectives and actual results.

### Step 7.2 — Final abstract & title
**Goal:** Refresh abstract/title now that results exist.
**Tasks:** regenerate abstract including key quantitative results (from computed data); word-limit enforcement per venue; title re-suggestion; show diff vs draft abstract.
**Acceptance:** Abstract numbers match results; within word limit.

### Step 7.3 — Conclusion & Synthesis agent
**Goal:** Coherent ending and cross-section check.
**Tasks:** coverage matrix Objective ↔ RQ ↔ Method ↔ Result ↔ Conclusion (from state + KG) displayed in UI; flag unanswered RQs; write conclusion (findings, contributions, limitations, future work); update KG (`Conclusion-[:BASED_ON]->Result`).
**Acceptance:** Coverage matrix has no unexplained gaps; approve.

---

## PHASE 8 — References

### Step 8.1 — Reference library & verification
**Goal:** Clean, verified bibliography source.
**Tasks:** build `references` from cited papers; verify/enrich metadata via Crossref (DOI) and Semantic Scholar; CSL-JSON per reference; duplicate detection (DOI/title); Zotero import (user API key) and BibTeX/RIS import; manual add.
**Acceptance:** All cited papers verified (green); duplicates merged; Zotero import works.

### Step 8.2 — Citation resolution & style formatting
**Goal:** Consistent citations across manuscript.
**Tasks:** scan all sections for `[@key]`; detect missing keys, uncited references, duplicates; render in-text citations and bibliography with citeproc (CSL styles: IEEE, APA, ACM, Springer, Elsevier/Harvard); style switcher preview.
**Tests:** unresolved key detection; style rendering snapshot tests.
**Acceptance:** Switching IEEE ↔ APA re-renders everything correctly; consistency report shows zero errors.

---

## PHASE 9 — Review & Formatting

### Step 9.1 — Manuscript Review/Consistency agent
**Goal:** Pre-export quality report.
**Tasks:** checks — section completeness; objective/method/result/conclusion consistency; numeric consistency; citation integrity; figure/table referenced in text; terminology consistency; word/page budget vs venue; LLM reviewer comments (clarity, structure) as suggestions. Report with severity levels and "jump to location"; one-click "apply suggestion" creates a new section version.
**Acceptance:** Deliberately inserted inconsistency (e.g., wrong accuracy number) is caught.

### Step 9.2 — Venue templates & configuration
**Goal:** Target-venue support.
**Tasks:** venue config schema (section order, heading style, abstract limit, page limit, column layout, citation style, figure rules); templates in `backend/templates/` for IEEE conference, Springer LNCS, ACM, Elsevier; allow uploading a custom LaTeX/DOCX template + pasted author guidelines (LLM extracts rules into venue config, researcher confirms).
**Acceptance:** Selecting a venue updates citation style and limits throughout the app.

### Step 9.3 — Export: LaTeX, DOCX, PDF
**Goal:** Submission-ready files.
**Tasks:** assemble approved sections in venue order; LaTeX via Jinja2 templates (escape special chars, figures, tables, `.bib`); DOCX via python-docx (styles, captions, numbering); PDF via latexmk (LaTeX path) or LibreOffice (DOCX path) inside the worker container; zip download (tex + bib + figures); export history.
**Acceptance:** Export the same project in IEEE and Springer formats; PDFs compile and open; DOCX opens cleanly in Word.

---

## PHASE 10 — Hardening & delivery

### Step 10.1 — Collaboration & sessions
**Tasks:** invite members (editor/viewer) by email; role checks on every endpoint; activity log per project; chat sessions per project (research Q&A grounded on project context + RAG).
**Acceptance:** Viewer cannot edit; editor can; activity log shows actions.

### Step 10.2 — Observability, cost & robustness
**Tasks:** agent run dashboard (tokens, cost estimate, latency, failures per agent); rate limiting; graceful error states in UI; retries for external APIs; per-project LLM budget warning.
**Acceptance:** Dashboard shows accurate counts after a full pipeline run.

### Step 10.3 — End-to-end test & demo project
**Tasks:** e2e test running the full pipeline with FakeLLM + recorded API fixtures; a seeded demo project; final README (architecture diagram, setup, env, screenshots); evaluation notes (citation accuracy, consistency checks, time saved) for the project report.
**Acceptance:** Fresh clone → `make up && make migrate && make seed` → demo project walks through all stages and exports a PDF.

---

# PART E — QUICK REFERENCE FOR THE AGENT

- **Current step:** always read from `PROGRESS.md`.
- **Never** proceed without `APPROVED X.Y`.
- **Never** invent citations, numbers, or user data.
- **Always** route LLM calls through `services/llm.py`, heavy work through Celery, and write `agent_runs`.
- **Always** finish a step with tests passing and a Handover Report.
- If unsure → ask.

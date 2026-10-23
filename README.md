# ResearchFlow

> Multi-Agent, Human-in-the-Loop AI Research Paper Assistant

ResearchFlow is an end-to-end, multi-agent, AI-powered research assistant that supports researchers through the **complete research-paper lifecycle**: idea & problem identification → literature survey → methodology → results & discussion → introduction → conclusion → references → review → formatting & export.

## Features

- **Multi-agent architecture** — specialized AI agents for each stage of paper development
- **Human-in-the-loop** — researcher reviews, edits, and approves every stage
- **Grounded generation** — RAG-based writing with verified citations only
- **Research gap detection** — identify candidate gaps from literature analysis
- **Knowledge graph** — Neo4j-powered visualization of literature relationships
- **Publication-ready export** — IEEE, Springer, ACM, Elsevier templates (LaTeX, DOCX, PDF)

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/) v2+
- [Python 3.11+](https://www.python.org/downloads/) (for running infrastructure check script)

### Setup

1. **Clone and configure:**
   ```bash
   git clone <repo-url>
   cd researchflow
   cp .env.example .env
   # Edit .env with your secrets (OPENAI_API_KEY, passwords, etc.)
   ```

2. **Start all services:**
   ```bash
   make up
   ```
   On Windows (no `make`), one command starts everything, applies migrations and opens http://localhost:3000:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start.ps1          # add -Build after frontend/dependency changes
   docker compose stop                                                 # stop everything
   ```

3. **Verify infrastructure:**
   ```bash
   python scripts/check_infra.py
   ```
   You should see `OK` for all services: PostgreSQL, pgvector, Redis, MinIO, Neo4j, GROBID.

4. **Access services:**
   | Service | URL |
   |---------|-----|
   | Backend API docs | http://localhost:8000/docs |
   | Frontend | http://localhost:3000 |
   | MinIO Console | http://localhost:9001 |
   | Neo4j Browser | http://localhost:7474 |

### Database & demo data

```bash
make migrate   # docker compose exec backend alembic upgrade head
make seed      # docker compose exec backend python -m app.db.seed
```

The seed creates a demo account: `demo@researchflow.dev` / `demo-password` (dev only).

### Makefile Commands

```bash
make up       # Start all services (builds images)
make down     # Stop all services
make logs     # Tail logs from all services
make ps       # Show service status
make migrate  # Apply database migrations
make seed     # Create the demo user + project
make test     # Backend (pytest) + frontend (vitest) tests
make worker   # Follow the Celery worker logs
make lint     # ruff + mypy (backend), tsc (frontend)
```

**No `make` (e.g. Windows)?** Run the commands directly:

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.db.seed
docker compose exec backend pytest
cd frontend && npm install && npm test
```

### AI model (Gemini by default)

Get a free API key at https://aistudio.google.com/apikey and put it in `.env`:

```bash
GEMINI_API_KEY=your-key
```

Then restart the backend and worker (`docker compose up -d backend worker`) and use **System → Test AI connection**.
To use OpenAI instead set `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, and the model names (see `.env.example`).
`LLM_PROVIDER=fake` runs everything without a key (placeholder AI output).

Note: on Gemini's free tier, prompts may be used by Google to improve its products; use a paid key for confidential research.

### Background jobs

Long-running work (paper search, PDF parsing, generation) runs on the Celery `worker` service, using Redis as the queue.
The worker restarts automatically when backend code changes. To check it works, open **System** in the app and start the demo job.

### Frontend development

The `frontend` container serves a production build. For hot reload while working on the UI:

```bash
docker compose stop frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```

## Architecture

```
Researcher
   │
Next.js Web App
   │  REST + SSE
FastAPI Backend ── Auth (JWT)
   │
Agent Orchestrator (LangGraph)
   │
   ├── Stage Agents (Idea, Literature, Methodology, Results, etc.)
   │
Shared Services (LLM, Embeddings, Academic APIs, PDF processing)
   │
Data Layer: PostgreSQL + pgvector │ Neo4j │ MinIO │ Redis
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI, SQLAlchemy 2.x |
| Orchestration | LangGraph |
| LLM | OpenAI API (configurable) |
| Vector DB | PostgreSQL + pgvector |
| Knowledge Graph | Neo4j |
| Task Queue | Celery + Redis |
| File Storage | MinIO |

## Project Structure

See [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) for the complete specification.

## License

TBD

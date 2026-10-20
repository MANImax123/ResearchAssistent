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

### Makefile Commands

```bash
make up       # Start all services
make down     # Stop all services
make logs     # Tail logs from all services
make ps       # Show service status
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

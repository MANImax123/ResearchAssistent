# Progress
Current step: 4.9

| Step | Title | Status | Approved on | Notes |
|------|-------|--------|-------------|-------|
| 0.1  | Repo & infra scaffolding | ✅ | 2026-10-20 | All 6 checks OK; MinIO via Bitnami image (see DECISIONS) |
| 0.2  | FastAPI backend skeleton | ✅ | 2026-10-20 | |
| 0.3  | Database models & migrations | ✅ | 2026-10-20 | |
| 0.4  | Authentication (JWT) | ✅ | 2026-10-20 | |
| 0.5  | Frontend skeleton + auth | ✅ | 2026-10-20 | |
| 1.1  | Projects CRUD + dashboard | ✅ | 2026-10-20 | |
| 1.2  | File uploads (MinIO) | ✅ | 2026-10-20 | |
| 1.3  | Celery background jobs + job status | ✅ | 2026-10-20 | |
| 1.4  | LLM & embeddings service | ✅ | 2026-10-20 | Gemini default (approved), OpenAI optional |
| 2.1  | ResearchState + LangGraph orchestrator skeleton | ✅ | 2026-10-21 | |
| 2.2  | Approval checkpoints & feedback actions | ✅ | 2026-10-21 | |
| 2.3  | Section editor with versioning | ✅ | 2026-10-21 | |
| 3.1  | Research Planning + idea refinement | ✅ | 2026-10-21 | First real agent (Gemini) |
| 3.2  | Critic agents (Novelty, Feasibility, Methodology) | ✅ | 2026-10-21 | Parallel critics; revision limited to selected fields; per-loop SDK clients |
| 3.3  | Draft abstract | ✅ | 2026-10-21 | Venue word range with length control; approved with the plan |
| 4.1  | Academic API clients + Paper Discovery agent | ✅ | 2026-10-21 | S2, OpenAlex, arXiv, Crossref; dedup; manual DOI/arXiv/PDF add |
| 4.2  | Relevance Ranking agent + selection | ✅ | 2026-10-21 | Embeddings + LLM rerank with reasons; selection → selected_paper_ids |
| 4.3  | PDF acquisition + Document Parsing agent | ✅ | 2026-10-21 | Legal OA PDFs; GROBID + PyMuPDF fallback; section chunks; upload-needed flow |
| 4.4  | Embeddings + RAG Retrieval agent | ✅ | 2026-10-21 | HNSW + MMR retrieval; Ask the literature with verified chunk citations |
| 4.5  | Paper Analysis agent | ✅ | 2026-10-21 | Evidence-backed profiles (every value cites passages); editable |
| 4.6  | Comparative Analysis agent | ✅ | 2026-10-21 | Grounded matrix of selected papers; custom AI columns; CSV; insert as table |
| 4.7  | Contradiction Detection agent | ✅ | 2026-10-22 | Dataset/metric groups; cross-paper passages and tables; persistent dismissals |
| 4.8  | Knowledge Graph agent + visualization | ✅ done | 2026-10-22 | Neo4j graph per project (papers, methods, datasets, metrics, limitations, conflicts, plan), entity merging, Graph tab with filters + node details |
| 4.9  | Research Gap Detection agent | 🔄 in progress | | |
| 4.10 | Objective/RQ refinement + Literature Survey section | ⬜ not started | | |
| 5.1  | Methodology input workspace | ⬜ not started | | |
| 5.2  | Diagram understanding | ⬜ not started | | |
| 5.3  | Methodology section generation | ⬜ not started | | |
| 6.1  | Results data ingestion & analysis | ⬜ not started | | |
| 6.2  | Tables & charts | ⬜ not started | | |
| 6.3  | Results & Discussion section | ⬜ not started | | |
| 7.1  | Introduction agent | ⬜ not started | | |
| 7.2  | Final abstract & title | ⬜ not started | | |
| 7.3  | Conclusion & Synthesis agent | ⬜ not started | | |
| 8.1  | Reference library & verification | ⬜ not started | | |
| 8.2  | Citation resolution & style formatting | ⬜ not started | | |
| 9.1  | Manuscript Review/Consistency agent | ⬜ not started | | |
| 9.2  | Venue templates & configuration | ⬜ not started | | |
| 9.3  | Export: LaTeX, DOCX, PDF | ⬜ not started | | |
| 10.1 | Collaboration & sessions | ⬜ not started | | |
| 10.2 | Observability, cost & robustness | ⬜ not started | | |
| 10.3 | End-to-end test & demo project | ⬜ not started | | |

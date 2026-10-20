# Decisions

All design decisions not explicitly stated in BUILD_INSTRUCTIONS.md are logged here.

| Date | Step | Decision | Rationale |
|------|------|----------|-----------|
| 2026-10-20 | 0.1 | Use `pgvector/pgvector:pg16` Docker image for Postgres with pgvector | Official pgvector image provides PostgreSQL 16 with the extension pre-installed (`ankane/pgvector` is the deprecated name) |
| 2026-10-20 | 0.1 | Use `lfoppiano/grobid:0.8.1` Docker image | Stable, well-tested GROBID release for PDF/TEI processing |
| 2026-10-20 | 0.1 | MinIO via `bitnamilegacy/minio:2025.7.23` (pinned); bucket auto-created with `MINIO_DEFAULT_BUCKETS` | Upstream stopped publishing `minio/minio` on Docker Hub and quay.io (2025). Bitnami's build is the same MinIO server, so the B9 stack is unchanged. Its built-in bucket creation replaces the separate `mc` init container |
| 2026-10-20 | 0.1 | MinIO web console as a separate `minio-console` service (`bitnamilegacy/minio-object-browser:2.0.2`), host port 9001 → 9090 | Bitnami's server image has no embedded console. Log in with `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` |
| 2026-10-20 | 0.1 | GROBID healthcheck uses bash `/dev/tcp` instead of curl | The `lfoppiano/grobid` image has no curl/wget, so the curl healthcheck always reported unhealthy |
| 2026-10-20 | 0.1 | `check_infra.py` maps Docker service hostnames (`postgres`, `redis`, …) to `localhost` and checks pgvector separately | `.env` uses in-network hostnames for the backend containers, while the script runs on the host |
| 2026-10-20 | 0.1 | Neo4j community edition (no APOC initially) | APOC can be added later if needed; community edition is free and sufficient for v1 |

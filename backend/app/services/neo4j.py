"""Neo4j access (Step 4.8). One async driver per event loop (workers run each job in a fresh loop).

The knowledge graph is a derived view of Postgres: every node carries `project_id`, and every query
in this codebase filters by it (B13), so projects never see each other's graph.
"""

from functools import lru_cache
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import get_settings
from app.services.llm_providers import PerLoop


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._drivers: PerLoop[AsyncDriver] = PerLoop(lambda: AsyncGraphDatabase.driver(uri, auth=(user, password)))

    async def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Run a query and return its records as dicts (reads and writes alike)."""
        result = await self._drivers.get().execute_query(query, params)
        return [record.data() for record in result.records]

    async def ensure_schema(self) -> None:
        """Lookups are by (project_id, key): index both for every label we write."""
        for label in LABELS:
            await self.run(
                f"CREATE INDEX kg_{label.lower()}_key IF NOT EXISTS FOR (n:{label}) ON (n.project_id, n.key)"
            )


LABELS = ("Project", "Paper", "Method", "Dataset", "Metric", "Limitation", "Objective", "ResearchQuestion", "Gap")


@lru_cache
def get_neo4j() -> Neo4jClient:
    settings = get_settings()
    return Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

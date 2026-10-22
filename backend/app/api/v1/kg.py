"""Knowledge graph (Step 4.8): build the project's graph and read subgraphs / node details."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.agents import registry
from app.api.v1.jobs import DispatcherDep, start_job
from app.core.deps import CurrentUser, DbSession
from app.db.models import Job
from app.db.models.enums import MemberRole
from app.schemas.job import JobRead
from app.services import knowledge_graph
from app.services.neo4j import LABELS
from app.services.projects import get_project_for_user

router = APIRouter(prefix="/projects/{project_id}/kg", tags=["knowledge graph"])


class GraphNode(BaseModel):
    id: str  # "Label:key"
    type: str
    label: str | None
    degree: int = 0
    props: dict[str, Any]


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    props: dict[str, Any]


class Subgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Neighbour(BaseModel):
    id: str
    type: str
    label: str | None
    relation: str
    outgoing: bool
    props: dict[str, Any]


class NodeDetails(BaseModel):
    id: str
    type: str
    label: str | None
    props: dict[str, Any]
    neighbours: list[Neighbour]


@router.post("/build", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def build_graph(project_id: uuid.UUID, user: CurrentUser, db: DbSession, dispatcher: DispatcherDep) -> Job:
    """Rebuild the graph from the selected papers, their profiles, contradictions and the research plan."""
    await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    return await start_job(db, dispatcher, user, "knowledge_graph", {"project_id": str(project_id)}, project_id)


@router.get("", response_model=Subgraph)
async def get_subgraph(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    types: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """This project's nodes (optionally only some types) and the edges between them."""
    await get_project_for_user(db, project_id, user)
    if types and (unknown := set(types) - set(LABELS)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown node types: {', '.join(sorted(unknown))}")
    return await knowledge_graph.subgraph(project_id, registry.graph_factory(), types or None)


@router.get("/nodes/{node_id}", response_model=NodeDetails)
async def get_node(project_id: uuid.UUID, node_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """A node's properties and its neighbours (e.g. which papers use a method)."""
    await get_project_for_user(db, project_id, user)
    label, _, key = node_id.partition(":")
    if label not in LABELS or not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    details = await knowledge_graph.node_details(project_id, registry.graph_factory(), label, key)
    if details is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    return details

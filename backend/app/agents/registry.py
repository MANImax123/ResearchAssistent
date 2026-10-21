"""Which agent runs each stage. Stages without a real agent yet use the stubs from the graph module."""

import uuid
from collections.abc import Callable

from app.agents.idea.agent import make_idea_agent
from app.core.config import get_settings
from app.db.models.enums import StageName
from app.orchestrator.graph import StageAgent
from app.services.context import format_context, load_context
from app.services.llm import LLMService, SessionFactory, get_provider
from app.services.storage import get_storage


def default_agents(session_factory: SessionFactory) -> dict[StageName, StageAgent]:
    """Real agents, wired to the LLM gateway, storage and the given database sessions."""

    def llm() -> LLMService:
        # Resolved when the agent runs, so a missing API key fails that run with a clear message
        return LLMService(get_provider(), get_settings(), session_factory)

    async def notes(project_id: uuid.UUID) -> str:
        async with session_factory() as session:
            return format_context(await load_context(session, get_storage(), project_id))

    return {StageName.IDEA: make_idea_agent(llm, notes)}


# Tests swap this for agents backed by a FakeLLM
agent_factory: Callable[[SessionFactory], dict[StageName, StageAgent]] = default_agents

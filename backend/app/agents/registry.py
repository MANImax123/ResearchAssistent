"""Which agent runs each stage. Stages without a real agent yet use the stubs from the graph module."""

import uuid
from collections.abc import Callable

from app.agents.idea.agent import make_idea_agent
from app.agents.idea.critique import CRITICS, CriticFn, make_llm_critic
from app.agents.literature.parsing import Tools
from app.agents.literature.survey import SurveyInputs, make_literature_agent
from app.core.config import get_settings
from app.db.models.enums import StageName
from app.orchestrator.graph import StageAgent
from app.services import survey
from app.services.academic.models import SearchSourceName
from app.services.academic.sources import SearchClient, default_clients
from app.services.context import format_context, load_context
from app.services.embeddings import EmbeddingService, get_embeddings
from app.services.grobid import GrobidClient
from app.services.llm import LLMService, SessionFactory, get_provider
from app.services.neo4j import Neo4jClient, get_neo4j
from app.services.pdf_fetch import PdfFetcher
from app.services.storage import get_storage


def default_llm(session_factory: SessionFactory) -> Callable[[], LLMService]:
    def llm() -> LLMService:
        # Resolved when the agent runs, so a missing API key fails that run with a clear message
        return LLMService(get_provider(), get_settings(), session_factory)

    return llm


def default_agents(session_factory: SessionFactory) -> dict[StageName, StageAgent]:
    """Real agents, wired to the LLM gateway, storage and the given database sessions."""

    async def notes(project_id: uuid.UUID) -> str:
        async with session_factory() as session:
            return format_context(await load_context(session, get_storage(), project_id))

    async def survey_inputs(project_id: uuid.UUID, selected: list[str], gap_ids: list[str]) -> SurveyInputs:
        return await survey.load_inputs(session_factory, project_id, selected, gap_ids)

    return {
        StageName.IDEA: make_idea_agent(llm_factory(session_factory), notes),
        StageName.LITERATURE: make_literature_agent(llm_factory(session_factory), survey_inputs),
    }


def default_critics(session_factory: SessionFactory) -> dict[str, CriticFn]:
    llm = llm_factory(session_factory)
    return {name: make_llm_critic(name, llm) for name in CRITICS}


# Tests swap these for FakeLLM-backed versions
agent_factory: Callable[[SessionFactory], dict[StageName, StageAgent]] = default_agents
llm_factory: Callable[[SessionFactory], Callable[[], LLMService]] = default_llm
critic_factory: Callable[[SessionFactory], dict[str, CriticFn]] = default_critics
academic_factory: Callable[[], dict[SearchSourceName, SearchClient]] = default_clients
embeddings_factory: Callable[[], EmbeddingService] = get_embeddings


def default_parsing_tools() -> Tools:
    settings = get_settings()
    return Tools(
        storage=get_storage(),
        fetcher=PdfFetcher(unpaywall_email=settings.unpaywall_email, max_bytes=settings.max_upload_mb * 1024 * 1024),
        grobid=GrobidClient(settings.grobid_url),
    )


parsing_factory: Callable[[], Tools] = default_parsing_tools
graph_factory: Callable[[], Neo4jClient] = get_neo4j

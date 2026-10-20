import uuid

from pydantic import BaseModel, Field


class LLMTestRequest(BaseModel):
    idea: str = Field(
        default=(
            "A multi-agent assistant that helps researchers write papers end to end, keeping a human in "
            "the loop at every stage and only citing papers it has verified."
        ),
        min_length=10,
        max_length=4000,
    )
    include_embedding: bool = True


class IdeaSnapshot(BaseModel):
    """Structured output the test asks the model for."""

    working_title: str = Field(min_length=3, max_length=200)
    keywords: list[str] = Field(min_length=3, max_length=6)
    research_questions: list[str] = Field(min_length=2, max_length=3)
    one_line_summary: str = Field(min_length=10, max_length=400)


class RunSummary(BaseModel):
    agent_run_id: uuid.UUID
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class EmbeddingSummary(BaseModel):
    provider: str
    model: str
    dimensions: int
    norm: float


class LLMTestResponse(BaseModel):
    data: IdeaSnapshot
    run: RunSummary
    embedding: EmbeddingSummary | None

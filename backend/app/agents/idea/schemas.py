"""The research plan produced by the Idea agent (Step 3.1) and edited by the researcher."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=250)]

OBJECTIVE_ID = re.compile(r"^O[1-9]\d*$")
QUESTION_ID = re.compile(r"^RQ[1-9]\d*$")


class Objective(BaseModel):
    id: str = Field(description='Sequential id: "O1", "O2", …')
    text: Text

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not OBJECTIVE_ID.match(v):
            raise ValueError('objective ids look like "O1", "O2", …')
        return v


class ResearchQuestion(BaseModel):
    id: str = Field(description='Sequential id: "RQ1", "RQ2", …')
    text: Text
    kind: Literal["question", "hypothesis"] = "question"
    linked_objective_ids: list[str] = Field(min_length=1, description="Ids of the objectives this question serves")

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not QUESTION_ID.match(v):
            raise ValueError('research question ids look like "RQ1", "RQ2", …')
        return v

    @model_validator(mode="after")
    def _hypothesis_is_a_statement(self) -> "ResearchQuestion":
        if self.kind == "hypothesis" and self.text.rstrip().endswith("?"):
            raise ValueError(
                f"{self.id} is marked as a hypothesis but is phrased as a question; "
                "state it as a testable claim, or mark it as a question"
            )
        return self


class IdeaPlan(BaseModel):
    """Everything the researcher reviews at the end of Step 3.1."""

    refined_idea: Text = Field(description="The idea restated precisely in 2-4 sentences")
    domain: Short
    keywords: list[Short] = Field(min_length=3, max_length=12)
    subtopics: list[Short] = Field(min_length=2, max_length=8)
    problem_statement: Text
    motivation: Text
    scope: Text = Field(description="What the work covers and, explicitly, what it does not")
    expected_contributions: list[Text] = Field(min_length=2, max_length=6)
    objectives: list[Objective] = Field(min_length=3, max_length=6)
    research_questions: list[ResearchQuestion] = Field(min_length=2, max_length=8)
    research_directions: list[Text] = Field(min_length=2, max_length=6, description="Possible directions, not claims")
    title_options: list[Title] = Field(min_length=3, max_length=3)
    title: Title | None = Field(default=None, description="The researcher's chosen title")

    @model_validator(mode="after")
    def _consistent_ids(self) -> "IdeaPlan":
        objective_ids = [o.id for o in self.objectives]
        if len(set(objective_ids)) != len(objective_ids):
            raise ValueError("objective ids must be unique")
        question_ids = [q.id for q in self.research_questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("research question ids must be unique")
        known = set(objective_ids)
        for q in self.research_questions:
            missing = [o for o in q.linked_objective_ids if o not in known]
            if missing:
                raise ValueError(f"{q.id} links to objectives that don't exist: {', '.join(missing)}")
        unused = known - {o for q in self.research_questions for o in q.linked_objective_ids}
        if unused:
            raise ValueError(
                f"every objective needs at least one research question; none for {', '.join(sorted(unused))}"
            )
        return self

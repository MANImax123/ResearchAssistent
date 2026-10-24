"""All ORM models. Importing this package registers every table on `Base.metadata`."""

from app.db.models.activity import AgentRun, Feedback, Job
from app.db.models.analysis import Comparison, Contradiction, PlanRefinement, ResearchGap
from app.db.models.figure import Figure, ManuscriptTable
from app.db.models.file import File
from app.db.models.methodology import MethodologyInput, MethodologyQuestion
from app.db.models.paper import Paper, PaperChunk, PaperDocument, PaperProfile, SearchQuery
from app.db.models.project import Project, ProjectMember, ResearchStateSnapshot, Stage
from app.db.models.reference import Reference
from app.db.models.section import Section, SectionVersion
from app.db.models.user import User

__all__ = [
    "AgentRun",
    "Comparison",
    "Contradiction",
    "Feedback",
    "Figure",
    "File",
    "Job",
    "ManuscriptTable",
    "Paper",
    "PaperChunk",
    "PaperDocument",
    "PaperProfile",
    "Project",
    "ProjectMember",
    "Reference",
    "MethodologyInput",
    "MethodologyQuestion",
    "PlanRefinement",
    "ResearchGap",
    "ResearchStateSnapshot",
    "SearchQuery",
    "Section",
    "SectionVersion",
    "Stage",
    "User",
]

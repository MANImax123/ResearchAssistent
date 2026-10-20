"""All ORM models. Importing this package registers every table on `Base.metadata`."""

from app.db.models.activity import AgentRun, Feedback, Job
from app.db.models.analysis import Comparison, Contradiction, ResearchGap
from app.db.models.figure import Figure, ManuscriptTable
from app.db.models.file import File
from app.db.models.paper import Paper, PaperChunk, PaperProfile
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
    "PaperProfile",
    "Project",
    "ProjectMember",
    "Reference",
    "ResearchGap",
    "ResearchStateSnapshot",
    "Section",
    "SectionVersion",
    "Stage",
    "User",
]

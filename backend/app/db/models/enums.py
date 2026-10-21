"""Enumerations shared by models, schemas, and the orchestrator."""

from enum import StrEnum


class StageName(StrEnum):
    """The 9 stages of the paper lifecycle (B8), in pipeline order."""

    IDEA = "idea"
    LITERATURE = "literature"
    METHODOLOGY = "methodology"
    RESULTS = "results"
    INTRODUCTION = "introduction"
    CONCLUSION = "conclusion"
    REFERENCES = "references"
    REVIEW = "review"
    FORMATTING = "formatting"


class StageStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPLETED = "completed"


class MemberRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class SectionType(StrEnum):
    TITLE = "title"
    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    LITERATURE_SURVEY = "literature_survey"
    METHODOLOGY = "methodology"
    RESULTS = "results"
    DISCUSSION = "discussion"
    CONCLUSION = "conclusion"
    REFERENCES = "references"


class SectionStatus(StrEnum):
    EMPTY = "empty"
    DRAFT = "draft"
    APPROVED = "approved"
    STALE = "stale"


class VersionSource(StrEnum):
    AI = "ai"
    USER = "user"
    AI_EDITED = "ai_edited"


class FileKind(StrEnum):
    PAPER_PDF = "paper_pdf"
    DATASET = "dataset"
    DIAGRAM = "diagram"
    CODE = "code"
    NOTES = "notes"
    RESULT = "result"


class PaperOrigin(StrEnum):
    SEARCH = "search"  # found by discovery
    MANUAL = "manual"  # added by DOI / arXiv id
    UPLOAD = "upload"  # the researcher uploaded the PDF


class QueryOrigin(StrEnum):
    AI = "ai"
    USER = "user"


class ParseStatus(StrEnum):
    NOT_STARTED = "not_started"
    UPLOAD_NEEDED = "upload_needed"
    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class GapConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GapStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class FeedbackAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    REGENERATE = "regenerate"
    BACK = "back"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

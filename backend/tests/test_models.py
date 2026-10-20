"""CRUD smoke tests for the ORM models against the migrated test database."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.models import (
    Paper,
    PaperChunk,
    PaperProfile,
    Project,
    Reference,
    ResearchGap,
    Section,
    SectionVersion,
    Stage,
    User,
)
from app.db.models.enums import GapConfidence, GapStatus, MemberRole, SectionType, StageName, StageStatus, VersionSource
from app.db.seed import DEMO_EMAIL, DEMO_PASSWORD, new_project, seed

DIM = get_settings().embedding_dim


def unit_vector(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


async def make_project(session: AsyncSession, email: str = "a@example.com") -> Project:
    user = User(email=email, name="A", password_hash=hash_password("pw"))
    session.add(user)
    await session.flush()
    project = new_project(user, "Test project", domain="NLP")
    session.add(project)
    await session.flush()
    return project


async def test_project_created_with_owner_and_all_stages(db_session: AsyncSession) -> None:
    project = await make_project(db_session)

    loaded = await db_session.scalar(
        select(Project)
        .where(Project.id == project.id)
        .options(selectinload(Project.stages), selectinload(Project.members))
        .execution_options(populate_existing=True)
    )

    assert loaded is not None
    assert loaded.current_stage == StageName.IDEA
    assert loaded.citation_style == "ieee"
    assert {s.name for s in loaded.stages} == set(StageName)
    assert all(s.status == StageStatus.NOT_STARTED for s in loaded.stages)
    assert [m.role for m in loaded.members] == [MemberRole.OWNER]


async def test_update_and_delete_cascades(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    paper = Paper(project_id=project.id, title="Attention Is All You Need", doi="10.48550/arXiv.1706.03762")
    paper.chunks = [PaperChunk(chunk_index=0, text="...", embedding=unit_vector(0))]
    paper.profile = PaperProfile(method="Transformer", dataset=["WMT14"])
    db_session.add(paper)
    await db_session.flush()

    project.title = "Renamed"
    await db_session.flush()
    assert await db_session.scalar(select(Project.title).where(Project.id == project.id)) == "Renamed"

    await db_session.delete(project)
    await db_session.flush()
    for model in (Stage, Paper, PaperChunk, PaperProfile):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


async def test_section_current_version_cycle(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    section = Section(project_id=project.id, type=SectionType.ABSTRACT)
    db_session.add(section)
    await db_session.flush()

    v1 = SectionVersion(section_id=section.id, content="Draft", source=VersionSource.AI)
    v2 = SectionVersion(section_id=section.id, content="Edited", source=VersionSource.USER)
    db_session.add_all([v1, v2])
    await db_session.flush()
    section.current_version_id = v2.id
    await db_session.flush()

    current = await db_session.scalar(
        select(SectionVersion.content).join(Section, Section.current_version_id == SectionVersion.id)
    )
    assert current == "Edited"


async def test_vector_similarity_search(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    paper = Paper(project_id=project.id, title="P")
    paper.chunks = [PaperChunk(chunk_index=i, text=f"chunk {i}", embedding=unit_vector(i)) for i in range(3)]
    db_session.add(paper)
    await db_session.flush()

    nearest = await db_session.scalar(
        select(PaperChunk.text).order_by(PaperChunk.embedding.cosine_distance(unit_vector(2))).limit(1)
    )
    assert nearest == "chunk 2"


async def test_jsonb_and_enum_round_trip(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    evidence = {"papers": ["p1"], "chunks": ["c1", "c2"]}
    gap = ResearchGap(project_id=project.id, statement="s", evidence=evidence, confidence=GapConfidence.MEDIUM)
    db_session.add(gap)
    await db_session.flush()
    await db_session.refresh(gap)

    assert gap.evidence == evidence
    assert gap.status == GapStatus.CANDIDATE


async def test_db_rejects_invalid_enum_value(db_session: AsyncSession) -> None:
    project = await make_project(db_session)

    with pytest.raises(IntegrityError, match="ck_stages_stagestatus"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE stages SET status = 'bogus' WHERE project_id = :p"), {"p": project.id}
            )


async def test_citation_key_unique_per_project(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    db_session.add(Reference(project_id=project.id, citation_key="vaswani2017", source="manual"))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(Reference(project_id=project.id, citation_key="vaswani2017", source="manual"))
            await db_session.flush()


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed(db_session)

    users = (await db_session.scalars(select(User).where(User.email == DEMO_EMAIL))).all()
    assert len(users) == 1
    assert verify_password(DEMO_PASSWORD, users[0].password_hash)
    projects = (await db_session.scalars(select(Project).where(Project.owner_id == users[0].id))).all()
    assert len(projects) == 1
    stage_count = await db_session.scalar(select(func.count()).where(Stage.project_id == projects[0].id))
    assert stage_count == len(StageName)


def test_password_hashing() -> None:
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-bcrypt-hash")
    assert verify_password("a" * 100, hash_password("a" * 100))  # >72 bytes doesn't raise

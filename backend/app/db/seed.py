"""Seed a demo user and project (idempotent).

python -m app.db.seed
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.models import Project, User
from app.db.session import SessionLocal, engine
from app.services.projects import new_project

log = get_logger(__name__)

DEMO_EMAIL = "demo@researchflow.dev"
DEMO_PASSWORD = "demo-password"  # dev-only demo account
DEMO_PROJECT_TITLE = "ResearchFlow: A Human-in-the-Loop Multi-Agent Research Paper Assistant"
DEMO_RAW_IDEA = (
    "An AI assistant that supports researchers through the whole paper lifecycle, from idea to formatted "
    "manuscript, using specialised agents that share one research state, with human approval at every stage."
)


async def seed(session: AsyncSession) -> None:
    user = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user is None:
        user = User(email=DEMO_EMAIL, name="Demo Researcher", password_hash=hash_password(DEMO_PASSWORD))
        session.add(user)
        await session.flush()
        log.info("seed_user_created", email=DEMO_EMAIL)

    project = await session.scalar(
        select(Project).where(Project.owner_id == user.id, Project.title == DEMO_PROJECT_TITLE)
    )
    if project is None:
        project = new_project(
            user,
            DEMO_PROJECT_TITLE,
            raw_idea=DEMO_RAW_IDEA,
            domain="Natural Language Processing",
            target_venue="ieee",
        )
        session.add(project)
        await session.flush()
        log.info("seed_project_created", project_id=str(project.id))

    await session.commit()


async def main() -> None:
    configure_logging(get_settings())
    async with SessionLocal() as session:
        await seed(session)
    await engine.dispose()
    log.info("seed_complete", email=DEMO_EMAIL)


if __name__ == "__main__":
    asyncio.run(main())

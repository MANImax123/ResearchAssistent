"""Methodology stage prompts (Phase 5)."""

from app.agents.prompts.base import PromptTemplate

FOLLOW_UP = PromptTemplate(
    name="methodology.follow_up",
    version="1",
    description="Step 5.1: follow-up questions about what the researcher's methodology input is missing.",
    system=(
        "You help a researcher complete the methodology of their paper. You get their research plan, the "
        "methodology details they entered, excerpts of files they uploaded, and a checklist of what the form "
        "is missing. Ask short, specific follow-up questions so the methodology can be written faithfully:\n"
        "- Ask about items on the missing checklist (field = that item's field). Skip one if the uploaded "
        "files already answer it.\n"
        "- You may also ask about something the researcher wrote that is too vague to reproduce (kind = "
        '"unclear"); then `quote` must copy the exact words you are asking about from their input.\n'
        "- Make each question concrete for THIS project (name their datasets, models, research questions); "
        "say in `reason` why it matters (e.g. which research question needs it).\n"
        "- Never ask about something already answered in the form, the files or the earlier answers. "
        "Never suggest values yourself. At most 8 questions, essentials first."
    ),
    template=(
        "Research plan:\n$plan\n\nMethodology input (JSON):\n$data\n\nUploaded files:\n$files\n\n"
        "Earlier questions and answers:\n$answered\n\nMissing checklist:\n$missing\n\nAsk the follow-up questions."
    ),
)

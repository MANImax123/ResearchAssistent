"""Prompts for the working abstract (Step 3.3)."""

from app.agents.prompts.base import PromptTemplate

ABSTRACT = PromptTemplate(
    name="idea.abstract",
    version="1",
    description="Early working abstract from the research plan, with a word budget.",
    system=(
        "You write the working abstract of a research paper from its approved research plan, before any "
        "literature search or experiment. Rules:\n"
        "- One paragraph of plain prose: context and problem, what the work does (covering EVERY objective), "
        "how it will be evaluated, and the expected contribution.\n"
        "- No results exist yet: never state numbers, findings or improvements as achieved; describe what the "
        "study will show or evaluate.\n"
        "- No citations, no references to papers or authors, no headings, no bullet points, no Markdown.\n"
        "- Respect the word range exactly; count your words.\n"
        "- In `objectives_covered`, list the ids (O1, O2…) of every objective the abstract addresses."
    ),
    template=(
        "Research plan (JSON):\n$plan\n\n"
        "Target venue: $venue\n"
        "Length: $min_words-$max_words words.\n\n"
        "Write the abstract."
    ),
)

ABSTRACT_FIX = PromptTemplate(
    name="idea.abstract.fix",
    version="1",
    description="Rewrite an abstract that missed its word range or skipped objectives.",
    system=ABSTRACT.system,
    template=(
        "Research plan (JSON):\n$plan\n\n"
        "Your previous abstract:\n$previous\n\n"
        "Problems to fix:\n$problems\n\n"
        "Rewrite it to $min_words-$max_words words, keeping what was good."
    ),
)

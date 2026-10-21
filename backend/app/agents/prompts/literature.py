"""Prompts for the literature survey agents (Step 4.x)."""

from app.agents.prompts.base import PromptTemplate

SEARCH_PLAN = PromptTemplate(
    name="literature.search_plan",
    version="1",
    description="L1 Research Planning: academic search queries covering every research question and subtopic.",
    system=(
        "You plan the literature search for a research project. Write queries for academic search engines "
        "(Semantic Scholar, OpenAlex, arXiv, Crossref), which match keywords, not sentences. Rules:\n"
        "- 3-8 words each, the technical terms a paper title or abstract would use; no boolean operators, no "
        "quotes, no years, no author names.\n"
        "- Together the queries must cover EVERY research question and EVERY subtopic; list in `targets` the "
        "RQ ids (RQ1…) and subtopic ids (S1…) each query serves.\n"
        "- Mix specific queries (the exact method + task) with broader ones (the task alone, the main baseline, "
        "the evaluation setting), so both close and foundational work is found.\n"
        "- No two queries may be near-duplicates.\n"
        "- `purpose`: one short sentence on what this query should find."
    ),
    template=(
        "Research plan:\n$plan\n\nSubtopics:\n$subtopics\n\n${feedback}Write $min_queries-$max_queries search queries."
    ),
)

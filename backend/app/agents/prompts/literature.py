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

RERANK = PromptTemplate(
    name="literature.rerank",
    version="1",
    description="L3 Relevance Ranking: judge each paper's relevance to the project's research questions.",
    system=(
        "You judge how relevant candidate papers are to a research project, using ONLY each paper's title and "
        "abstract. Rules:\n"
        "- score 0-10: 9-10 directly addresses a research question (same task and approach); 6-8 clearly useful "
        "(same task or method, a key baseline, dataset or evaluation); 3-5 background only; 0-2 off-topic.\n"
        "- `rq_ids`: the research questions (RQ1…) the paper informs; empty if none.\n"
        "- `reason`: one sentence naming what in the title/abstract makes it relevant or not. Never claim results "
        "or details the abstract doesn't state. If there is no abstract, judge from the title and say so.\n"
        "- Judge every paper listed, using its id (P1…), exactly once."
    ),
    template=(
        "Research questions:\n$questions\n\nObjectives:\n$objectives\n\nCandidate papers:\n$papers\n\nJudge each paper."
    ),
)

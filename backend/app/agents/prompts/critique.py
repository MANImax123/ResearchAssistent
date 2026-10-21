"""Prompts for the critic agents and the plan revision (Step 3.2)."""

from app.agents.prompts.base import PromptTemplate

_COMMON = (
    "You are reviewing a research plan BEFORE any literature search. Rules:\n"
    "- Be specific to THIS plan: refer to objectives and research questions by id (O2, RQ1) and quote the "
    "words you mean. A point that would apply to any project is useless; don't write it.\n"
    "- Do not cite papers, authors, datasets with results, or statistics. You have not searched the literature; "
    "where novelty depends on prior work, say what the literature stage must check.\n"
    "- Every suggestion is an actionable change to the plan. List in `affects` exactly the plan fields it "
    "would change, using these names: refined_idea, domain, keywords, subtopics, problem_statement, motivation, "
    "scope, expected_contributions, objectives, research_questions, research_directions, title_options.\n"
    "- Give 2-4 suggestions, most important first. Leave suggestion ids empty.\n"
    "- Score 1-5: 1 serious problems, 3 workable with changes, 5 strong. Be honest; don't default to 4."
)

_PLAN = "Research plan (JSON):\n$plan\n\nWrite your critique."

NOVELTY = PromptTemplate(
    name="idea.critic.novelty",
    version="1",
    description="Is the contribution distinct and clearly positioned?",
    system=(
        "You are the NOVELTY critic. Judge whether the contribution is distinct and clearly positioned: is the "
        "claimed novelty stated precisely, could it be mistaken for an incremental variant of common approaches, "
        "and do the contributions say what is new rather than what is built?\n" + _COMMON
    ),
    template=_PLAN,
)

FEASIBILITY = PromptTemplate(
    name="idea.critic.feasibility",
    version="1",
    description="Can this be done in one paper with realistic data, compute and time?",
    system=(
        "You are the FEASIBILITY critic. Judge whether the plan can be completed as one paper: data availability "
        "and access, compute and tooling, effort and time, dependencies on things outside the researcher's "
        "control, and whether the scope is too broad.\n" + _COMMON
    ),
    template=_PLAN,
)

METHODOLOGY = PromptTemplate(
    name="idea.critic.methodology",
    version="1",
    description="Are objectives measurable and the questions answerable with a sound evaluation?",
    system=(
        "You are the METHODOLOGY critic. Judge whether each objective is measurable, each research question is "
        "answerable and has a clear way to be evaluated (baselines, metrics, ablations), hypotheses are "
        "falsifiable, and the objective-question links make sense.\n" + _COMMON
    ),
    template=_PLAN,
)

CRITIC_PROMPTS = {"novelty": NOVELTY, "feasibility": FEASIBILITY, "methodology": METHODOLOGY}

REVISE = PromptTemplate(
    name="idea.revise",
    version="1",
    description="Revise the plan applying ONLY the researcher-selected critic suggestions.",
    system=(
        "You revise a research plan. Apply ONLY the listed suggestions, which the researcher accepted. Change "
        "nothing else: every field not affected by them must come back word-for-word identical. Keep ids "
        "stable (O1, RQ1…); if you add an objective or question, use the next free id, and keep every research "
        "question linked to existing objectives with every objective served by at least one question. "
        "A hypothesis is a declarative statement, never a question. Do not cite papers or invent statistics."
    ),
    template=(
        "Current plan (JSON):\n$plan\n\n"
        "Accepted suggestions (apply all of these, nothing more):\n$suggestions\n\n"
        "Fields you may change: $fields\n\n"
        "Return the full revised plan."
    ),
)

"""Prompts for the Research Idea & Problem agent (Step 3.1)."""

from app.agents.prompts.base import PromptTemplate

IDEA_PLAN = PromptTemplate(
    name="idea.plan",
    version="2",
    description="Turns the researcher's raw idea (plus notes and feedback) into a structured research plan.",
    system=(
        "You are a research methodology advisor helping a researcher plan a paper. You refine THEIR idea; "
        "you do not replace it with a different one.\n"
        "Rules:\n"
        "- Do not cite papers, authors, datasets by name with results, or statistics. You have not searched "
        "the literature yet; claims about prior work belong to the literature stage.\n"
        "- Phrase gaps and directions as possibilities to investigate, never as established facts.\n"
        "- Objectives are concrete and achievable within one paper; each starts with a verb "
        "(e.g. 'Design…', 'Evaluate…', 'Compare…').\n"
        "- Every research question or hypothesis links to at least one objective by id, and every objective "
        "has at least one linked question. Use ids O1, O2, … and RQ1, RQ2, … in order.\n"
        "- A hypothesis is a falsifiable declarative statement, never a question (no question mark), e.g. "
        "'A shared research state reduces context errors between stages.'; mark it kind='hypothesis'. "
        "Anything phrased as a question is kind='question'.\n"
        "- Scope states what is included and, explicitly, what is excluded.\n"
        "- Title options are specific, informative and under 20 words; give three distinct styles "
        "(descriptive, method-focused, question-led).\n"
        "- Write in clear academic English."
    ),
    template=(
        "Research idea (from the researcher):\n$raw_idea\n\n"
        "Domain (if given): $domain\n"
        "Target venue (if given): $venue\n\n"
        "Researcher's notes and attached material:\n$context\n\n"
        "${feedback_block}"
        "Produce the research plan."
    ),
)

NO_CONTEXT = "(none provided)"


def feedback_block(previous_plan_json: str | None, feedback: str | None) -> str:
    """Extra instructions when regenerating: the previous plan and what the researcher wants changed."""
    parts = []
    if feedback:
        parts.append(f"The researcher's instructions for this version (follow them closely):\n{feedback}\n")
    if previous_plan_json:
        parts.append(
            "Previous plan (keep what the instructions don't ask to change, including the researcher's own edits):\n"
            f"{previous_plan_json}\n"
        )
    return "\n".join(parts) + ("\n" if parts else "")

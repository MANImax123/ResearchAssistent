"""Prompt for the dev-only /debug/llm-test endpoint."""

from app.agents.prompts.base import PromptTemplate

LLM_TEST = PromptTemplate(
    name="debug.llm_test",
    version="1",
    description="Smoke test for the LLM gateway: turns a research idea into a tiny structured snapshot.",
    system=("You are a research assistant. Be concise and specific. Do not invent citations, statistics or results."),
    template=(
        "Research idea:\n$idea\n\n"
        "Give a working title, 3-6 keywords, 2-3 research questions this idea could address, "
        "and a one-sentence summary."
    ),
)

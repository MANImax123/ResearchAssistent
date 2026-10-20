"""Versioned prompt templates (A3: prompts live here, not inline in agent logic).

Templates use `$name` / `${name}` placeholders (string.Template), so prompts can contain JSON
examples with literal braces. Rendering fails loudly on missing or unexpected variables.

    IDEA_REFINE = PromptTemplate(name="idea.refine", version="1", system="...", template="Idea: $idea")
    llm.generate_structured(IDEA_REFINE.render(idea=raw), Plan, agent="idea", system=IDEA_REFINE.system,
                            prompt_id=IDEA_REFINE.id)
"""

from dataclasses import dataclass, field
from string import Template


class PromptRenderError(ValueError):
    pass


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    template: str
    system: str | None = None
    description: str = ""
    _compiled: Template = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        compiled = Template(self.template)
        if not compiled.is_valid():
            raise PromptRenderError(f"Prompt {self.id}: invalid placeholder syntax")
        object.__setattr__(self, "_compiled", compiled)

    @property
    def id(self) -> str:
        """Stable identifier stored with each agent run, e.g. `idea.refine@2`."""
        return f"{self.name}@{self.version}"

    @property
    def variables(self) -> set[str]:
        return set(self._compiled.get_identifiers())

    def render(self, **values: object) -> str:
        missing = self.variables - values.keys()
        unexpected = values.keys() - self.variables
        if missing or unexpected:
            raise PromptRenderError(
                f"Prompt {self.id}: missing {sorted(missing) or '-'}, unexpected {sorted(unexpected) or '-'}"
            )
        return self._compiled.substitute({k: str(v) for k, v in values.items()})

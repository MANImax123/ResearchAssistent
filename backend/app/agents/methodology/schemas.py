"""The researcher's methodology input (Step 5.1) and what it is still missing.

The form is the researcher's content (A4.5): agents structure and phrase it later but never change its
meaning. `missing()` is a deterministic check; the follow-up agent may only ask about what it finds
(or quote something specific it finds unclear), so its questions stay about what is actually missing.
"""

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Medium = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class DatasetInfo(BaseModel):
    name: Short
    source: Short = ""  # URL, citation or "collected by us"
    size: Short = ""  # e.g. "30,056 drug–target pairs"
    features: Medium = ""
    notes: Medium = ""


class Hyperparameter(BaseModel):
    name: Short
    value: Short
    notes: Short = ""


class Tool(BaseModel):
    name: Short
    version: Short = ""
    purpose: Short = ""


class Metric(BaseModel):
    name: Short
    notes: Short = ""  # definition, why it was chosen


class MethodologyInputData(BaseModel):
    architecture: LongText = ""
    algorithms: LongText = ""
    workflow: LongText = ""
    datasets: list[DatasetInfo] = Field(default_factory=list, max_length=30)
    preprocessing: LongText = ""
    experimental_setup: LongText = ""  # splits, baselines, hardware, number of runs
    hyperparameters: list[Hyperparameter] = Field(default_factory=list, max_length=100)
    tools: list[Tool] = Field(default_factory=list, max_length=60)
    metrics: list[Metric] = Field(default_factory=list, max_length=30)
    reproducibility: LongText = ""  # code/data availability, seeds, environment
    notes: LongText = ""  # anything else, free text


Field_ = Literal[
    "architecture", "algorithms", "workflow", "datasets", "preprocessing", "experimental_setup",
    "hyperparameters", "tools", "metrics", "reproducibility",
]  # fmt: skip

FIELD_LABEL: dict[str, str] = {
    "architecture": "System architecture",
    "algorithms": "Algorithms / model",
    "workflow": "Workflow",
    "datasets": "Datasets",
    "preprocessing": "Preprocessing",
    "experimental_setup": "Experimental setup",
    "hyperparameters": "Hyperparameters",
    "tools": "Tools, frameworks and libraries",
    "metrics": "Evaluation metrics",
    "reproducibility": "Reproducibility",
}

# Without these the Methodology section can't be written faithfully
ESSENTIAL = {"algorithms", "datasets", "experimental_setup", "metrics"}

_SPLIT = re.compile(
    r"\b(split|train(ing)?|test(ing)?|validation|held[- ]out|cross[- ]validat|k[- ]fold|fold|scaffold)\b", re.I
)


@dataclass(frozen=True)
class Missing:
    field: str
    reason: str
    essential: bool


def missing(data: MethodologyInputData, *, diagrams: int = 0, code_files: int = 0) -> list[Missing]:
    """What the input doesn't say yet (essentials first). Uploaded diagrams count as an architecture
    description; uploaded code counts towards reproducibility."""
    found: list[Missing] = []

    def add(field: str, reason: str) -> None:
        found.append(Missing(field, reason, field in ESSENTIAL))

    if not data.algorithms:
        add("algorithms", "No algorithm or model is described.")
    if not data.datasets:
        add("datasets", "No dataset is listed.")
    else:
        for d in data.datasets:
            gaps = [label for label, value in (("source", d.source), ("size", d.size)) if not value]
            if gaps:
                add("datasets", f"Dataset “{d.name}” has no {' or '.join(gaps)}.")
    if not data.experimental_setup:
        add("experimental_setup", "The experimental setup (splits, baselines, runs) isn't described.")
    elif not _SPLIT.search(data.experimental_setup):
        add("experimental_setup", "The setup doesn't say how the data is split for training and testing.")
    if not data.metrics:
        add("metrics", "No evaluation metric is given.")
    if not data.architecture and not diagrams:
        add("architecture", "No system architecture is described or uploaded as a diagram.")
    if not data.workflow:
        add("workflow", "The end-to-end workflow isn't described.")
    if not data.preprocessing:
        add("preprocessing", "Data preprocessing isn't described.")
    if not data.hyperparameters:
        add("hyperparameters", "No hyperparameters are given.")
    if not data.tools:
        add("tools", "No tools, frameworks or libraries are listed.")
    if not data.reproducibility and not code_files:
        add("reproducibility", "Nothing is said about code/data availability, seeds or the environment.")
    found.sort(key=lambda m: not m.essential)
    return found

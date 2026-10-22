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

ASK = PromptTemplate(
    name="literature.ask",
    version="1",
    description="L5 RAG: answer a question from retrieved passages only, citing them.",
    system=(
        "You answer questions about a research project's literature using ONLY the numbered passages given. "
        "Rules:\n"
        "- Every factual sentence ends with the passage ids it relies on, like [S2] or [S1][S4]. Use only ids "
        "from the list; never cite anything else, never invent papers, numbers or datasets.\n"
        '- Name the paper when it helps ("GraphDTA uses …"), but the [S#] marker is what counts as the citation.\n'
        "- If the passages don't answer the question, or only partly, say so plainly and set `insufficient` to "
        "true; don't fill gaps from general knowledge.\n"
        "- Be concise: a short paragraph or a few bullet points."
    ),
    template="Question: $question\n\nPassages:\n$passages\n\nAnswer the question from these passages.",
)

ANALYZE = PromptTemplate(
    name="literature.analyze",
    version="1",
    description="L6 Paper Analysis: a structured profile of one paper, every field citing its passages.",
    system=(
        "You build a structured profile of ONE research paper from its numbered passages [C1]…[Cn]. Rules:\n"
        "- Use only what the passages state. Never add knowledge about the paper from memory.\n"
        '- Every field and every list item cites the passage ids it comes from in `evidence` (e.g. ["C3", '
        '"C7"]). No evidence, no item.\n'
        "- problem: the problem the paper addresses (1-2 sentences). method: what they propose/do (2-3 "
        "sentences). results: the main quantitative/qualitative results as stated, with numbers if given "
        "(2-3 sentences).\n"
        "- datasets: names of datasets/benchmarks used. metrics: evaluation metrics. limitations: limitations "
        "the AUTHORS state about their own work (not limitations of prior work they overcome). tech_stack: "
        "models, libraries, tools or hardware named.\n"
        "- Leave a text field null and a list empty when the passages don't say."
    ),
    template="Paper: $title\n\nPassages:\n$passages\n\nProfile this paper.",
)

COMPARE = PromptTemplate(
    name="literature.compare",
    version="1",
    description="L7 Comparative Analysis: concise comparison-table cells per paper.",
    system=(
        "You fill a literature comparison table. For each paper (P1…), write short table cells from the "
        "facts given ONLY:\n"
        "- method: the approach in at most 12 words. dataset: dataset/benchmark names, comma-separated. "
        "metric: evaluation metric names, comma-separated. result: the headline result in at most 20 words, "
        "keeping the exact numbers given. limitation: the main stated limitation in at most 15 words.\n"
        "- Never add facts, numbers, datasets or metrics that are not in the given text. Leave a cell empty "
        "if the paper's facts don't state it.\n"
        "- Return one row per paper with its id."
    ),
    template="Papers:\n$papers\n\nFill the table.",
)

COMPARE_COLUMN = PromptTemplate(
    name="literature.compare_column",
    version="1",
    description="L7: answer a researcher-defined comparison column per paper from its passages.",
    system=(
        "You fill one column of a literature comparison table. For each paper (P1…), answer the column's "
        "question in at most 15 words using ONLY that paper's passages, and list the passage ids you used "
        "(e.g. P2-C1). If its passages don't say, return an empty value."
    ),
    template="Column: $column\n\nPapers and their passages:\n$papers\n\nFill the column.",
)

CONTRADICTIONS = PromptTemplate(
    name="literature.contradictions",
    version="1",
    description="L8 Contradiction Detection: conflicting claims between papers that study the same thing.",
    system=(
        "You check papers that report on the same topic for CONFLICTING claims. Compare every pair of papers "
        "(P1 vs P2, P1 vs P3, …) using only their passages. Rules:\n"
        "- A contradiction is two claims that cannot both be true as stated: opposite conclusions about the same "
        "method or effect, or clearly different numbers for the same model/baseline on the same dataset and "
        "metric. Different papers simply reporting their own (different) models is NOT a contradiction.\n"
        "- Quote or closely paraphrase each claim, and list the passage ids supporting it (claim A from paper A's "
        "passages only, claim B from paper B's).\n"
        "- Give a possible explanation and its kind: different_setup, dataset_version, metric_definition, "
        "data_split, genuine_disagreement or unclear.\n"
        "- Return an empty list if there is no real conflict. Never invent claims or numbers."
    ),
    template="Topic: $topic\n\nPapers and passages:\n$papers\n\nList the contradictions.",
)

GAPS = PromptTemplate(
    name="literature.gaps",
    version="1",
    description="L10 Research Gap Detection: candidate gaps from limitations, conflicts, untested pairs, future work.",
    system=(
        "You help a researcher spot POTENTIAL research gaps in the papers they selected. You only see signals "
        "taken from those papers: limitations (L#), contradictions (C#), method × dataset combinations none of "
        "the selected papers report (U#) and future-work statements (F#). Rules:\n"
        "- Each gap must be specific: name the concrete method, dataset, metric, population or setting.\n"
        "- Phrase every gap as a possibility, never as a fact: 'may', 'appears', 'it is unclear whether', "
        "'little evidence among the selected papers'. Never write 'no study has', 'proves' or 'confirmed'.\n"
        "- The rationale says how the cited signals point to the gap and what work could address it.\n"
        "- `sources` lists the signal ids the gap rests on (at least one). Never cite an id that isn't listed.\n"
        "- Confidence: high only when several independent papers point to it; medium for one strong signal or "
        "two weaker ones; low otherwise. An untested combination alone is at most low.\n"
        "- A U# signal may name a library or tool rather than a method (e.g. PyTorch): ignore those.\n"
        "- `objective_ids`: the plan objectives (O#) the gap could serve; empty if none fits.\n"
        "- Prefer gaps that matter for the research plan. Don't repeat gaps the researcher already decided on.\n"
        "- Return 3 to 8 gaps; fewer only if the signals don't support 3 distinct ones."
    ),
    template=(
        "Research plan objectives:\n$objectives\n\nResearch questions:\n$questions\n\n"
        "Gaps the researcher already accepted or rejected (don't repeat):\n$decided\n\n"
        "Signals from the selected papers:\n$signals\n\nList the potential gaps."
    ),
)

GAPS_REWORD = PromptTemplate(
    name="literature.gaps_reword",
    version="1",
    description="L10: rephrase gap statements that read as certain facts into possibilities.",
    system=(
        "Rephrase each statement as a possibility rather than a fact, keeping every specific (method, dataset, "
        "metric, setting) and its meaning. Use wording like 'may', 'appears', 'it is unclear whether' or "
        "'little evidence among the selected papers'. Never use 'no study has', 'proves' or 'confirmed'."
    ),
    template="Statements:\n$statements\n\nReturn each one rephrased, with its number.",
)

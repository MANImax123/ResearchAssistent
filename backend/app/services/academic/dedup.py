"""Deduplicate papers found by several sources and queries.

Two records are the same paper when they share an identifier (DOI, arXiv id, Semantic Scholar id,
OpenAlex id), or, failing that, when their titles match closely (normalised, difflib ratio ≥ 0.93)
and their years are at most one apart. Different non-arXiv DOIs always mean different papers (e.g. a
conference paper and its journal extension); a preprint server's DOI (arXiv, bioRxiv/medRxiv, SSRN,
Research Square, ChemRxiv, OSF, Preprints.org) doesn't block a title match, so a preprint merges with
its published version.
"""

from difflib import SequenceMatcher

from app.services.academic.models import PaperMetadata, normalize_title

TITLE_SIMILARITY = 0.93
ID_FIELDS = ("doi", "arxiv_id", "s2_id", "openalex_id")
PREPRINT_DOI_PREFIXES = (
    "10.48550/",  # arXiv
    "10.1101/",  # bioRxiv, medRxiv
    "10.2139/",  # SSRN
    "10.21203/",  # Research Square
    "10.26434/",  # ChemRxiv
    "10.31219/",  # OSF Preprints
    "10.20944/",  # Preprints.org
)


def is_preprint_doi(doi: str | None) -> bool:
    return bool(doi) and doi.startswith(PREPRINT_DOI_PREFIXES)  # type: ignore[union-attr]


PREPRINT_LAG_YEARS = 3  # a preprint can appear in a journal years later


def _is_preprint(paper: PaperMetadata) -> bool:
    return is_preprint_doi(paper.doi) or (paper.arxiv_id is not None and (not paper.doi or paper.venue == "arXiv"))


def _years_compatible(a: PaperMetadata, b: PaperMetadata) -> bool:
    """Same year ±1; a preprint may precede its published version by up to PREPRINT_LAG_YEARS."""
    if a.year is None or b.year is None or abs(a.year - b.year) <= 1:
        return True
    earlier, later = (a, b) if a.year < b.year else (b, a)
    return _is_preprint(earlier) and not _is_preprint(later) and later.year - earlier.year <= PREPRINT_LAG_YEARS  # type: ignore[operator]


def _dois_conflict(a: PaperMetadata, b: PaperMetadata) -> bool:
    return bool(a.doi and b.doi and a.doi != b.doi and not is_preprint_doi(a.doi) and not is_preprint_doi(b.doi))


def same_title(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b or abs(len(a) - len(b)) > 0.1 * max(len(a), len(b)):
        return False
    matcher = SequenceMatcher(None, a, b)
    return matcher.quick_ratio() >= TITLE_SIMILARITY and matcher.ratio() >= TITLE_SIMILARITY


def merge(base: PaperMetadata, other: PaperMetadata) -> PaperMetadata:
    """Fill gaps in `base` from `other`; combine sources and the queries that found them."""
    data = base.model_dump()
    for field in (*ID_FIELDS, "year", "url", "pdf_url"):
        if data[field] is None:
            data[field] = getattr(other, field)
    # The published version's DOI, year and venue beat the preprint's
    if is_preprint_doi(base.doi) and other.doi and not is_preprint_doi(other.doi):
        data["doi"] = other.doi
        data["year"] = other.year or base.year
        if other.venue:
            data["venue"] = other.venue
    if other.abstract and len(other.abstract) > len(base.abstract or ""):
        data["abstract"] = other.abstract
    if other.venue and (not base.venue or base.venue == "arXiv"):
        data["venue"] = other.venue
    if len(other.authors) > len(base.authors):
        data["authors"] = [a.model_dump() for a in other.authors]
    counts = [c for c in (base.citation_count, other.citation_count) if c is not None]
    data["citation_count"] = max(counts) if counts else None
    data["sources"] = list(dict.fromkeys([*base.sources, *other.sources]))
    data["found_by"] = list(dict.fromkeys([*base.found_by, *other.found_by]))
    return PaperMetadata.model_validate(data)


class Deduplicator:
    """Accumulates papers; `add` returns the index of the record the paper was merged into."""

    def __init__(self, existing: list[PaperMetadata] | None = None) -> None:
        self.papers: list[PaperMetadata] = []
        self._ids: dict[tuple[str, str], int] = {}
        self._titles: list[str] = []
        self.existing_count = 0
        self.updated_existing: set[int] = set()  # existing papers that new results added information to
        for paper in existing or []:
            self.add(paper)
        self.existing_count = len(self.papers)

    def _find(self, paper: PaperMetadata) -> int | None:
        for field in ID_FIELDS:
            value = getattr(paper, field)
            if value and (field, value) in self._ids:
                index = self._ids[(field, value)]
                if field == "doi" or not _dois_conflict(self.papers[index], paper):
                    return index
        title = normalize_title(paper.title)
        for index, other_title in enumerate(self._titles):
            other = self.papers[index]
            if same_title(title, other_title) and _years_compatible(paper, other) and not _dois_conflict(paper, other):
                return index
        return None

    def _index(self, index: int) -> None:
        paper = self.papers[index]
        for field in ID_FIELDS:
            if value := getattr(paper, field):
                self._ids.setdefault((field, value), index)

    def add(self, paper: PaperMetadata) -> int:
        index = self._find(paper)
        if index is None:
            self.papers.append(paper)
            self._titles.append(normalize_title(paper.title))
            index = len(self.papers) - 1
        else:
            merged = merge(self.papers[index], paper)
            if index < self.existing_count and merged != self.papers[index]:
                self.updated_existing.add(index)
            self.papers[index] = merged
        self._index(index)
        return index

    @property
    def new_papers(self) -> list[PaperMetadata]:
        return self.papers[self.existing_count :]

"""Per-venue writing limits used before full venue configuration exists (Step 9.2 replaces this).

Only limits stated in the venues' own author guidelines are listed; everything else uses the default.
"""

DEFAULT_ABSTRACT_WORDS: tuple[int, int] = (200, 250)

ABSTRACT_WORDS: dict[str, tuple[int, int]] = {
    "ieee": (150, 250),
    "springer_lncs": (150, 250),
}


def abstract_word_limits(venue: str | None) -> tuple[int, int]:
    """(min, max) words for the abstract at this venue."""
    return ABSTRACT_WORDS.get(venue or "", DEFAULT_ABSTRACT_WORDS)

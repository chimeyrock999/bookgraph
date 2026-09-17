"""Page-span arithmetic shared by the segmenters.

A segmenter that bounds a section by the *next* section's start page has to guard
the same two underflows (a shared start page, out-of-order pages, page 0), so the
guard lives here once: a fix lands for every segmenter instead of only the one it
was reported against.
"""

from __future__ import annotations


def clamp_page_end(page_start: int | None, page_end: int | None) -> int | None:
    """Return a ``page_end`` that never runs backwards and is never negative.

    ``page_end`` derived as ``next_start_page - 1`` underflows below ``page_start``
    when two sections begin on the same page or the parser emits pages out of order,
    and underflows below zero when the next section starts on page 0 — both produce a
    span no real reading unit can have (issue #38).
    """

    if page_end is None:
        return None
    if page_start is not None:
        page_end = max(page_start, page_end)
    return max(0, page_end)

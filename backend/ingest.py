"""PDF -> pages -> chunks.

Tables are rendered as pipe-delimited rows rather than left to flatten into
prose, because a bare number that has lost its row and column labels cannot be
turned into a grounded fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pdfplumber

PAGE_MARK = "[[page {n}]]"
_PAGE_MARK_RE = re.compile(r"\[\[page (\d+)\]\]")


@dataclass
class Page:
    number: int          # 1-indexed, matches what a reader sees in the PDF
    text: str
    tables_text: str

    @property
    def combined(self) -> str:
        return self.text + ("\n" + self.tables_text if self.tables_text else "")


@dataclass
class Chunk:
    index: int
    start_page: int
    end_page: int
    text: str            # includes [[page N]] markers so the model can cite precisely


def _render_tables(page) -> str:
    out = []
    try:
        tables = page.extract_tables()
    except Exception:
        return ""
    for t in tables:
        rows = [
            " | ".join((c or "").strip().replace("\n", " ") for c in row)
            for row in t
            if any(c and c.strip() for c in row)
        ]
        if rows:
            out.append("TABLE:\n" + "\n".join(rows))
    return "\n".join(out)


def extract_pages(path: str, max_pages: int | None = None) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages, start=1):
            if max_pages and i > max_pages:
                break
            try:
                text = p.extract_text() or ""
            except Exception:
                text = ""
            pages.append(Page(number=i, text=text, tables_text=_render_tables(p)))
    return pages


def chunk_pages(pages: list[Page], pages_per_chunk: int = 10) -> list[Chunk]:
    chunks: list[Chunk] = []
    for idx, start in enumerate(range(0, len(pages), pages_per_chunk)):
        group = [p for p in pages[start : start + pages_per_chunk] if p.combined.strip()]
        if not group:
            continue
        body = "\n\n".join(PAGE_MARK.format(n=p.number) + "\n" + p.combined for p in group)
        chunks.append(
            Chunk(
                index=idx,
                start_page=group[0].number,
                end_page=group[-1].number,
                text=body,
            )
        )
    return chunks


def doc_head(pages: list[Page], n: int = 3, limit: int = 6000) -> str:
    """Opening pages, used for the document-context pass (title, publisher, dates)."""
    return "\n\n".join(p.combined for p in pages[:n])[:limit]


def page_text_map(pages: list[Page]) -> dict[int, str]:
    """Page number -> text, used by the grounding verifier."""
    return {p.number: p.combined for p in pages}

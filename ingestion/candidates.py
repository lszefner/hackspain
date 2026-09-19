"""Deterministic candidate generation for the Jev interpretation adapter.

Candidates are deliberately small, source-linked spans.  The generator never
looks at benchmark references or tries to decide which invoice field a span
means; that remains a Jev Choice decision followed by deterministic assembly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/. -]\d{1,2}[/. -]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}\s+(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|"
    r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?\d{4})\b",
    re.IGNORECASE,
)
_IBAN_RE = re.compile(r"\b[A-Z]{2}\s?\d{2}(?:(?:\s?[A-Z0-9]{4}){3,7})\b", re.IGNORECASE)
_TAX_ID_RE = re.compile(r"\b[ABCDEFGHJNPQRSUVW]?\s?\d{7,8}\s?[A-Z]?\b", re.IGNORECASE)
_NUMBER_RE = re.compile(
    r"(?<![\w.,])[-+]?(?:\d{1,3}(?:[. ]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?:%)?(?![\w.,])"
)
_CURRENCY_RE = re.compile(r"(?i)(?<![A-Za-z])(EUR|€|USD|GBP)(?![A-Za-z])")
_INVOICE_LABEL_RE = re.compile(
    r"(?i)(?:factura|invoice|n[úuº°o]?m(?:ero)?|n[.º°o])\s*[:#-]?\s*"
    r"([A-Z0-9][A-Z0-9./_-]{1,})"
)

_KIND_ORDER = {
    "text": 0,
    "identifier": 1,
    "date": 2,
    "iban": 3,
    "tax_id": 4,
    "amount": 5,
    "currency": 6,
}


class CandidateOverflowError(ValueError):
    """Raised when a provider Choice question cannot contain all candidates."""

    code = "candidate_overflow"

    def __init__(self, count: int, limit: int, *, field: str | None = None) -> None:
        self.count = count
        self.limit = limit
        self.field = field
        suffix = f" for {field}" if field else ""
        super().__init__(
            f"{count} candidates exceed the Choice limit of {limit}{suffix}"
        )


def _context(text: str, start: int, end: int, window: int = 80) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    return text[left:right]


def _candidate(
    *,
    candidate_id: str,
    text: str,
    page: int,
    reference_id: str,
    start: int,
    end: int,
    kind: str,
    source: str,
    context: str | None = None,
) -> dict[str, Any]:
    """Create the stable public candidate representation."""

    return {
        "id": candidate_id,
        "text": text,
        "kind": kind,
        "page": page,
        "reference_id": reference_id,
        "start": start,
        "end": end,
        "span": {"start": start, "end": end},
        "source": source,
        "context": context if context is not None else text,
    }


def _add_span_candidates(
    output: list[dict[str, Any]],
    *,
    prefix: str,
    text: str,
    page: int,
    reference_id: str,
    source: str,
) -> None:
    """Append conservative typed spans, deduplicating exact source regions."""

    seen: set[tuple[str, int, int]] = set()

    def add(kind: str, match: re.Match[str], group: int = 0) -> None:
        start, end = match.span(group)
        if start < 0 or end <= start:
            return
        value = match.group(group).strip()
        key = (kind, start, end)
        if not value or key in seen:
            return
        seen.add(key)
        output.append(
            _candidate(
                candidate_id=f"{prefix}-s{len(output):04d}",
                text=value,
                page=page,
                reference_id=reference_id,
                start=start,
                end=end,
                kind=kind,
                source=source,
                context=_context(text, start, end),
            )
        )

    protected: list[tuple[int, int]] = []
    # Literal substrings let the model select a description/name independently
    # of neighbouring quantities, labels and amounts. Never cross a source line
    # or table cell; every option keeps coordinates in the original reading.
    offset = 0
    for line in text.splitlines(keepends=True):
        literal = line.rstrip("\r\n")
        spans = {(0, len(literal))}
        tokens = list(re.finditer(r"\S+", literal))
        for index, token in enumerate(tokens):
            for last in tokens[index : index + 8]:
                spans.add((token.start(), last.end()))
        for match in re.finditer(r"[^|:;\t]+", literal):
            spans.add(match.span())
        for start, end in sorted(spans):
            while start < end and literal[start].isspace():
                start += 1
            while end > start and literal[end - 1].isspace():
                end -= 1
            if start == end:
                continue
            candidates_start, candidates_end = offset + start, offset + end
            output.append(
                _candidate(
                    candidate_id=f"{prefix}-textspan-{candidates_start}-{candidates_end}",
                    text=text[candidates_start:candidates_end],
                    page=page,
                    reference_id=reference_id,
                    start=candidates_start,
                    end=candidates_end,
                    kind="text",
                    source=source,
                    context=_context(text, candidates_start, candidates_end),
                )
            )
        offset += len(line)
    for match in _INVOICE_LABEL_RE.finditer(text):
        add("identifier", match, 1)
        protected.append(match.span(1))
    for match in _IBAN_RE.finditer(text):
        add("iban", match)
        protected.append(match.span())
    for match in _DATE_RE.finditer(text):
        add("date", match)
        protected.append(match.span())
    for match in _CURRENCY_RE.finditer(text):
        add("currency", match)
    for match in _TAX_ID_RE.finditer(text):
        value = match.group(0).strip()
        # A tax-id candidate must contain either a letter or a label nearby;
        # otherwise ordinary seven/eight digit invoice numbers are too noisy.
        before = text[max(0, match.start() - 12) : match.start()].lower()
        if any(token in before for token in ("nif", "cif", "vat", "tax")) or re.search(
            r"[A-Za-z]", value
        ):
            add("tax_id", match)
    for match in _NUMBER_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in protected):
            continue
        add("amount", match)


def _iter_blocks(
    reading: Mapping[str, Any],
) -> Iterable[tuple[int, Mapping[str, Any], str, str]]:
    for page_obj in reading.get("pages", []) or []:
        if not isinstance(page_obj, Mapping):
            continue
        page = int(page_obj.get("page", 0) or 0)
        for block in page_obj.get("blocks", []) or []:
            if not isinstance(block, Mapping):
                continue
            block_id = str(block.get("id", ""))
            if not block_id:
                continue
            text = str(block.get("text", "") or "")
            yield page, block, block_id, text


def build_candidates(
    reading: Mapping[str, Any], *, context_window: int = 80
) -> list[dict[str, Any]]:
    """Build stable block, cell, and typed-span candidates from a reading.

    Block and cell offsets are relative to their own literal ``text`` values;
    they are exact even when the OCR reading contains repeated text.  IDs are
    deterministic within one reading artifact and retain their source IDs.
    """

    del context_window  # reserved for a future versioned context policy
    candidates: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, int, int]] = set()

    for page, block, block_id, text in _iter_blocks(reading):
        candidates.append(
            _candidate(
                candidate_id=block_id,
                text=text,
                page=page,
                reference_id=block_id,
                start=0,
                end=len(text),
                kind="text",
                source="block",
                context=text,
            )
        )

        # Party names/addresses may occupy only one literal line or a labelled
        # value in a page-wide OCR block. Preserve offsets instead of selecting
        # the entire page as a scalar field.
        offset = 0
        for line_index, line in enumerate(text.splitlines(keepends=True)):
            literal = line.rstrip("\r\n")
            spans = [(0, len(literal))]
            if ":" in literal:
                start = literal.index(":") + 1
                while start < len(literal) and literal[start].isspace():
                    start += 1
                spans.append((start, len(literal)))
            for span_index, (start, end) in enumerate(spans):
                if literal[start:end].strip():
                    candidates.append(
                        _candidate(
                            candidate_id=f"{block_id}-text-{line_index}-{span_index}",
                            text=literal[start:end],
                            page=page,
                            reference_id=block_id,
                            start=offset + start,
                            end=offset + end,
                            kind="text",
                            source="line",
                            context=_context(text, offset + start, offset + end),
                        )
                    )
            offset += len(line)
        rows = block.get("rows", []) or []
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                continue
            row_id = str(row.get("id", f"{block_id}-r{row_index:03d}"))
            for cell_index, cell in enumerate(row.get("cells", []) or [], start=1):
                if not isinstance(cell, Mapping):
                    continue
                cell_id = str(cell.get("id", f"{row_id}-c{cell_index:03d}"))
                cell_text = str(cell.get("text", "") or "")
                candidates.append(
                    _candidate(
                        candidate_id=cell_id,
                        text=cell_text,
                        page=page,
                        reference_id=cell_id,
                        start=0,
                        end=len(cell_text),
                        kind="text",
                        source="cell",
                        context=cell_text,
                    )
                )
                _add_span_candidates(
                    candidates,
                    prefix=cell_id,
                    text=cell_text,
                    page=page,
                    reference_id=cell_id,
                    source="cell",
                )

        _add_span_candidates(
            candidates,
            prefix=block_id,
            text=text,
            page=page,
            reference_id=block_id,
            source="block",
        )

    # Ensure stable ordering: source document order first, then span kind and
    # offset.  Reassigning generated span IDs after sorting would make source
    # IDs unstable, so IDs remain tied to creation order and are deterministic.
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (
            str(candidate["reference_id"]),
            str(candidate["kind"]),
            int(candidate["start"]),
            int(candidate["end"]),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(candidate)
    return unique


def candidates_by_kind(
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Index candidates without changing their IDs, spans, or ordering."""

    indexed: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        kind = str(candidate.get("kind", "text"))
        indexed.setdefault(kind, []).append(dict(candidate))
    return indexed


def build_line_groups(reading: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return ordered, source-linked line groups for Jev's per-line choices.

    A table row remains one group even when its description is repeated.  For
    plain OCR blocks, each non-empty literal line is a separate group with an
    exact offset in the block text.
    """

    groups: list[dict[str, Any]] = []
    for page, block, block_id, text in _iter_blocks(reading):
        rows = block.get("rows", []) or []
        if rows:
            for row_index, row in enumerate(rows, start=1):
                if not isinstance(row, Mapping):
                    continue
                row_id = str(row.get("id", f"{block_id}-r{row_index:03d}"))
                cells = [
                    cell
                    for cell in row.get("cells", []) or []
                    if isinstance(cell, Mapping)
                ]
                row_text = " | ".join(
                    str(cell.get("text", "") or "") for cell in cells
                ).strip()
                if not row_text:
                    continue
                groups.append(
                    {
                        "id": f"{row_id}-line",
                        "page": page,
                        "reference_ids": [str(cell.get("id", row_id)) for cell in cells]
                        or [row_id],
                        "text": row_text,
                        "start": 0,
                        "end": len(row_text),
                        "candidate": {
                            "id": f"{row_id}-line",
                            "text": row_text,
                            "kind": "line",
                            "page": page,
                            "reference_id": row_id,
                            "start": 0,
                            "end": len(row_text),
                            "span": {"start": 0, "end": len(row_text)},
                            "source": "row",
                            "context": row_text,
                        },
                    }
                )
            continue
        lines = text.splitlines() or ([text] if text else [])
        offset = 0
        for line_index, line in enumerate(lines, start=1):
            start = text.find(line, offset)
            if start < 0:
                start = offset
            end = start + len(line)
            offset = end + 1
            if not line.strip():
                continue
            line_id = f"{block_id}-l{line_index:03d}"
            groups.append(
                {
                    "id": line_id,
                    "page": page,
                    "reference_ids": [block_id],
                    "text": line,
                    "start": start,
                    "end": end,
                    "candidate": {
                        "id": line_id,
                        "text": line,
                        "kind": "line",
                        "page": page,
                        "reference_id": block_id,
                        "start": start,
                        "end": end,
                        "span": {"start": start, "end": end},
                        "source": "line",
                        "context": _context(text, start, end),
                    },
                }
            )
    return groups


def ensure_choice_capacity(
    candidates: Iterable[Mapping[str, Any]],
    limit: int = 255,
    *,
    field: str | None = None,
) -> list[dict[str, Any]]:
    """Validate provider Choice capacity and return a copy of the candidates."""

    result = [dict(candidate) for candidate in candidates]
    if len(result) > limit:
        raise CandidateOverflowError(len(result), limit, field=field)
    return result


# Compatibility aliases make the module easy to use from a CLI or tests while
# keeping one implementation and one candidate representation.
make_candidates = build_candidates
generate_candidates = build_candidates

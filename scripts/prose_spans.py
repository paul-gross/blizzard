"""Prose-span extraction and site attribution for `.py`, `.ts`, `.md` (issue #273).

The one place that knows how to find prose in a file and what to call the site it
found. Consumed by ``scripts/restated_invariants.py``; not refactored onto
``scripts/prose_density.py`` — that module's own extractors back a committed
baseline whose counts are load-bearing, and folding this one on top of it risks
changing them for no benefit this issue asks for.

Site identity is ``file`` + ``symbol``, never a line number: line numbers churn on
every edit and would make a registry keyed on them a maintenance tax.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

MODULE_SYMBOL = "<module>"
LEAD_SYMBOL = "<lead>"


@dataclass(frozen=True)
class ProseSpan:
    file: str  # path as given via the scanned root (e.g. "src/blizzard/wire/chunk.py")
    symbol: str  # stable site identity — see module docstring
    start_line: int
    text: str  # raw span text, newlines preserved


def extract_spans(path: Path) -> list[ProseSpan]:
    """Dispatch by extension; an unrecognized extension yields no spans."""
    return extract_spans_result(path)[0]


def extract_spans_result(path: Path) -> tuple[list[ProseSpan], bool]:
    """Like `extract_spans`, plus whether the file was skipped (unreadable, or a
    `.py` file that failed to parse) rather than genuinely carrying no prose —
    callers that want a skipped-file count use this; `extract_spans` collapses
    both cases to an empty list, as an unrecognized extension already does."""
    suffix = path.suffix
    if suffix == ".py":
        return _extract_py_spans(path)
    if suffix == ".ts":
        return _extract_ts_spans(path)
    if suffix == ".md":
        return _extract_md_spans(path)
    return [], False


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------


def _extract_py_spans(path: Path) -> tuple[list[ProseSpan], bool]:
    rel = str(path)
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        spans = _py_docstring_spans(tree, rel)
        spans.extend(_py_comment_spans(src, tree, rel))
    except (SyntaxError, tokenize.TokenError, OSError):
        return [], True
    return spans, False


def _py_docstring_spans(tree: ast.Module, rel: str) -> list[ProseSpan]:
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if not (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            continue
        doc = body[0].value
        symbol = MODULE_SYMBOL if isinstance(node, ast.Module) else node.name
        spans.append(ProseSpan(file=rel, symbol=symbol, start_line=doc.lineno, text=doc.value))
    return spans


def _py_symbol_at(tree: ast.Module, lineno: int) -> str:
    """The innermost class/function enclosing `lineno`, else `<module>`."""
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        start = node.lineno
        end = node.end_lineno or start
        if start <= lineno <= end:
            width = end - start
            if best is None or width < best[0]:
                best = (width, node.name)
    return best[1] if best else MODULE_SYMBOL


def _py_comment_spans(src: str, tree: ast.Module, rel: str) -> list[ProseSpan]:
    """Runs of consecutive full-line `#` comments, tokenize-derived."""
    lines = src.splitlines(keepends=True)
    full_comment_lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT and lines[tok.start[0] - 1].lstrip().startswith("#"):
            full_comment_lines.add(tok.start[0])

    spans: list[ProseSpan] = []
    start = prev = None
    for n in sorted(full_comment_lines):
        if start is None or n != prev + 1:
            if start is not None:
                spans.append(_make_py_comment_span(rel, tree, lines, start, prev))
            start = n
        prev = n
    if start is not None:
        spans.append(_make_py_comment_span(rel, tree, lines, start, prev))
    return spans


def _make_py_comment_span(rel: str, tree: ast.Module, lines: list[str], start: int, end: int) -> ProseSpan:
    text = "".join(lines[start - 1 : end])
    return ProseSpan(file=rel, symbol=_py_symbol_at(tree, start), start_line=start, text=text)


# --------------------------------------------------------------------------
# TypeScript
# --------------------------------------------------------------------------

_TS_BLOCK_RE = re.compile(r"/\*\*.*?\*/", re.DOTALL)
_TS_DECORATOR_RE = re.compile(r"^\s*@[A-Za-z_$][\w$]*")
_TS_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:class|interface|enum|type|function|const|let|var)"
    r"\s+([A-Za-z_$][\w$]*)"
)
_TS_IMPORT_RE = re.compile(r"^\s*(?:import\b|export\s+.*\bfrom\b)")


def _extract_ts_spans(path: Path) -> tuple[list[ProseSpan], bool]:
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return [], True
    rel = str(path)
    blanked = _ts_blank_literals(src)

    block_matches = list(_TS_BLOCK_RE.finditer(blanked))
    block_spans = [_ts_block_span(blanked, src, m) for m in block_matches]

    without_blocks = _blank_ranges(blanked, [(m.start(), m.end()) for m in block_matches])
    line_spans = _ts_line_comment_runs(without_blocks, src)

    all_spans = sorted(block_spans + line_spans, key=lambda s: s[0])
    lines = blanked.splitlines(keepends=True)

    result: list[ProseSpan] = []
    for idx, (start_line, end_line, text) in enumerate(all_spans):
        next_start = all_spans[idx + 1][0] if idx + 1 < len(all_spans) else None
        enclosing = _ts_nearest_enclosing(lines, start_line)
        symbol = _ts_symbol(lines, end_line, next_start, enclosing)
        result.append(ProseSpan(file=rel, symbol=symbol, start_line=start_line, text=text))
    return result, False


def _ts_block_span(blanked: str, src: str, m: re.Match[str]) -> tuple[int, int, str]:
    start_line = blanked.count("\n", 0, m.start()) + 1
    end_line = blanked.count("\n", 0, max(m.end() - 1, m.start())) + 1
    return start_line, end_line, src[m.start() : m.end()]


def _ts_blank_literals(src: str) -> str:
    """Blank string/template literal contents in place, line count preserved.

    Comments (`//` and `/* */`) are left untouched so their own text — which may
    contain apostrophes or quotes — is never misread as a string delimiter.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        two = src[i : i + 2]
        if two == "//":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if two == "/*":
            i += 2
            while i < n and src[i : i + 2] != "*/":
                i += 1
            i = min(i + 2, n)
            continue
        c = src[i]
        if c in ("'", '"'):
            start = i
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
            i = min(i + 1, n)
            _blank_range(out, start, i)
            continue
        if c == "`":
            start = i
            i += 1
            depth = 0
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "`" and depth == 0:
                    i += 1
                    break
                if src[i : i + 2] == "${":
                    depth += 1
                    i += 2
                    continue
                if src[i] == "}" and depth > 0:
                    depth -= 1
                    i += 1
                    continue
                i += 1
            _blank_range(out, start, i)
            continue
        i += 1
    return "".join(out)


def _blank_range(out: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if out[k] != "\n":
            out[k] = " "


def _blank_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    out = list(text)
    for start, end in ranges:
        _blank_range(out, start, end)
    return "".join(out)


def _ts_line_comment_runs(blanked: str, src: str) -> list[tuple[int, int, str]]:
    lines2 = blanked.splitlines()
    src_lines = src.splitlines(keepends=True)
    runs: list[tuple[int, int, str]] = []
    i, n = 0, len(lines2)
    while i < n:
        if lines2[i].strip().startswith("//"):
            start = i
            while i < n and lines2[i].strip().startswith("//"):
                i += 1
            runs.append((start + 1, i, "".join(src_lines[start:i])))
        else:
            i += 1
    return runs


def _ts_nearest_enclosing(lines: list[str], start_line: int) -> str:
    """Nearest declaration textually preceding `start_line` (a fallback, not
    brace-aware — a same-scope approximation, not full nesting resolution)."""
    for idx in range(start_line - 2, -1, -1):
        m = _TS_DECL_RE.match(lines[idx])
        if m:
            return m.group(1)
    return MODULE_SYMBOL


def _ts_symbol(lines: list[str], end_line: int, next_span_start: int | None, enclosing: str) -> str:
    """Forward attribution: from the span's end, walk forward past blank lines and
    a decorator run to the next declaration; fall through to `enclosing` (or
    `<module>`) on the next span, an import line, EOF, or (outside decorator mode)
    any other non-blank line."""
    n = len(lines)
    i = end_line  # 0-indexed position of the line right after the span
    decorator_mode = False
    while i < n:
        lineno = i + 1
        if next_span_start is not None and lineno >= next_span_start:
            break
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _TS_IMPORT_RE.match(line):
            break
        m = _TS_DECL_RE.match(line)
        if m:
            return m.group(1)
        if _TS_DECORATOR_RE.match(line):
            decorator_mode = True
            i += 1
            continue
        if decorator_mode:
            i += 1
            continue
        break
    return enclosing


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def slug(heading_text: str) -> str:
    """The GitHub heading-anchor algorithm: lowercase, drop everything but
    `a-z0-9 -`, then turn each remaining space into a hyphen."""
    lowered = heading_text.lower()
    kept = re.sub(r"[^a-z0-9 -]", "", lowered)
    return kept.replace(" ", "-")


def _blank_fences(src: str) -> str:
    lines = src.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            out.append("\n" if line.endswith("\n") else "")
            in_fence = not in_fence
            continue
        out.append("\n" if in_fence and line.endswith("\n") else ("" if in_fence else line))
    return "".join(out)


def _extract_md_spans(path: Path) -> tuple[list[ProseSpan], bool]:
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return [], True
    rel = str(path)
    blanked = _blank_fences(src)
    lines = blanked.splitlines(keepends=True)

    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((idx, slug(m.group(2))))

    spans: list[ProseSpan] = []
    lead_end = headings[0][0] if headings else len(lines)
    lead_text = "".join(lines[:lead_end])
    if lead_text.strip():
        spans.append(ProseSpan(file=rel, symbol=LEAD_SYMBOL, start_line=1, text=lead_text))

    for i, (idx, symbol) in enumerate(headings):
        start = idx + 1
        end = headings[i + 1][0] if i + 1 < len(headings) else len(lines)
        text = "".join(lines[start:end])
        if text.strip():
            spans.append(ProseSpan(file=rel, symbol=symbol, start_line=start + 1, text=text))
    return spans, False


def resolve_md_scope(path: Path, anchor: str) -> str | None:
    """Owner resolution for a `.md#anchor` citation: the section body under the
    heading whose slug matches `anchor`, down to the next heading of equal or
    higher level. `None` when no heading slugs to `anchor`."""
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return None
    blanked = _blank_fences(src)
    lines = blanked.splitlines(keepends=True)

    headings: list[tuple[int, int, str]] = []  # (0-indexed line, level, slug)
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((idx, len(m.group(1)), slug(m.group(2))))

    for i, (idx, level, sym) in enumerate(headings):
        if sym != anchor:
            continue
        end = len(lines)
        for j in range(i + 1, len(headings)):
            if headings[j][1] <= level:
                end = headings[j][0]
                break
        return "".join(lines[idx + 1 : end])
    return None


def resolve_code_scope(path: Path, symbol: str) -> str | None:
    """Owner resolution for a `.py#symbol`/`.ts#symbol` citation: the concatenated
    text of every prose span the extractor attributes to `symbol` in `path`.
    `None` when the file yields no such span."""
    matched = [s.text for s in extract_spans(path) if s.symbol == symbol]
    if not matched:
        return None
    return "\n".join(matched)

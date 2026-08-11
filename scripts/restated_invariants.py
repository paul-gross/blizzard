"""The restated-invariant sweep (issue #273): one home per fact, everything else a
pointer — `blizzard-context:/standards/one-prose-home.md`.

discover ROOTS...                                   run the four candidate detectors.
measure ROOTS... [--write-sites] [--markdown]        per-fact observed site inventory.
check ROOTS... [--strict] [--owners --context-root PATH]
                                                      exit 1 on drift, 2 on a bad registry.

The committed census/input is `scripts/restated-invariants.json`. A fact's ``markers``
are literal, space-normalized phrases matched with `re.search` against
`normalize()`d span text — self-normalization rules out regex metacharacters in
practice — 4-10 words of the fact's own content, never its name:
`validate_registry` rejects one that normalizes to a single token, matches a bare
``[\\w.]+`` name, or does not normalize to itself, and warns (does not reject) on
one outside the 4-10 word range unless the fact's ``marker_acknowledgments`` names
why. A fact's ``owner`` is either a `blizzard-context:/path#anchor` citation (needs
`--context-root`) or an in-repo `path#symbol`/`path#anchor`; ``owner_assert.quote``
must appear, normalized, in the resolved owner scope, or ``owner_assert.anchor:
true`` with a ``reason`` stands in for a quote that would be unwieldy to pin.
`--write-sites`'s preservation contract is `apply_write_sites`'s own docstring;
site identity (``file`` + ``symbol``, never a line number) is `prose_spans.py`'s.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import prose_spans
from prose_spans import ProseSpan

REGISTRY = Path(__file__).with_name("restated-invariants.json")

_EXTS = (".py", ".ts", ".md")

# Mirrors of an owner, not independent sites — always excluded (§The canonical root
# list). `**/node_modules/` matches on any path component; the rest are prefixes.
_EXCLUDED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("openapi",),
    ("web", "projects", "fleet", "src", "lib", "api"),
    ("src", "blizzard", "static"),
)

_OWNER_KINDS = {"domain", "wire", "seam", "module", "test", "doc-section"}
_ROLES = {"owner", "allowed"}

SiteKey = tuple[str, str]


class RegistryError(Exception):
    """A malformed registry or an illegal marker — a config error, not a finding."""


class OwnerResolutionError(Exception):
    """An owner citation that does not resolve, or whose assertion does not hold."""


class RootResolutionError(Exception):
    """An argv root that does not resolve to an existing directory, or to a single file of a
    swept extension — refused, never skipped, so a typo'd root in `mise.toml` cannot report a
    permanently-clean sweep."""


@dataclass(frozen=True)
class Finding:
    kind: str  # "new" | "stale" | "strict" | "owner" | "config" | "skipped"
    fact_id: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.fact_id}: {self.detail}"


# --------------------------------------------------------------------------
# File walking
# --------------------------------------------------------------------------


def _is_excluded(path: Path) -> bool:
    """A generated mirror per §The canonical root list. The prefix is matched as a
    contiguous subsequence of the path's parts, not only at index 0, so this holds
    for both a real repo-relative root (where the prefix starts the path) and a
    scratch-root fixture (where it appears somewhere inside a tmp_path)."""
    parts = path.parts
    if "node_modules" in parts:
        return True
    for prefix in _EXCLUDED_PREFIXES:
        plen = len(prefix)
        if any(parts[i : i + plen] == prefix for i in range(len(parts) - plen + 1)):
            return True
    return False


def iter_files(roots: list[str]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            raise RootResolutionError(f"root {root!r} does not resolve")
        if root_path.is_file():
            # A single-file root — `blizzard/README.md` is one, the only bound
            # surface that is not a tree. `rglob` on a file yields nothing, so
            # without this branch such a root sweeps zero files and reports
            # permanently clean, the failure this class exists to refuse.
            if root_path.suffix not in _EXTS:
                raise RootResolutionError(f"file root {root!r} is not one of {', '.join(_EXTS)}")
            if _is_excluded(root_path):
                # Silently contributing nothing is the very failure this class refuses:
                # the root resolves, the sweep reads zero files, and the report is a
                # permanent green. A directory root may legitimately contain excluded
                # files; a file root that IS one is a mistake in the argument.
                raise RootResolutionError(f"file root {root!r} is an excluded generated-output path")
            files.add(root_path)
            continue
        for ext in _EXTS:
            for f in root_path.rglob(f"*{ext}"):
                if f.is_file() and not _is_excluded(f):
                    files.add(f)
    return sorted(files)


def collect_spans(roots: list[str]) -> tuple[list[ProseSpan], int]:
    """All spans over `roots`, plus a count of files skipped (unreadable, or a `.py`
    file that failed to parse) rather than genuinely carrying no prose."""
    spans: list[ProseSpan] = []
    skipped = 0
    for f in iter_files(roots):
        file_spans, was_skipped = prose_spans.extract_spans_result(f)
        spans.extend(file_spans)
        skipped += was_skipped
    return spans, skipped


# --------------------------------------------------------------------------
# Normalization and markers
# --------------------------------------------------------------------------

_INLINE_CODE_RE = re.compile(r"``[^`]*``|`[^`]*`")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip inline-code spans (RST double-backtick and single-backtick
    alike — the double-backtick branch is tried first so `` ``Field`` `` doesn't
    leave its content exposed to matching in a `.py` docstring while an equivalent
    single-backtick `` `Field` `` in a `.ts`/`.md` host is stripped), collapse
    punctuation and whitespace — what makes `wrapped\\n * implies raw` match
    `wrapped implies raw`."""
    text = text.lower()
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _marker_illegality_reason(marker: str) -> str | None:
    """`None` when `marker` is a legal marker; otherwise the specific reason it
    isn't, named rather than collapsed to one message: a construction site would
    fire on a single-token or bare-field-name marker, and a not-self-normalized one
    silently never matches (`re.search` runs the raw marker against `normalize()`d
    span text, so a marker that isn't already normalized text is dead on arrival)."""
    normalized = normalize(marker)
    if not normalized or " " not in normalized:
        return "normalizes to a single token"
    if normalized != marker:
        return "does not normalize to itself (capitals, punctuation, or excess whitespace)"
    if re.fullmatch(r"[\w.]+", marker):
        return "looks like a bare field/dotted name"
    return None


def _marker_word_count_warning(fid: str, marker: str, ack_reason: str | None) -> str | None:
    """4-10 words of the fact's own content is the authoring guideline; outside that
    range is a warning, not a config error, unless `ack_reason` (the fact's
    `marker_acknowledgments[marker]`) names why a shorter/longer marker is
    deliberate — recorded once at authoring time rather than reprinted on every
    clean run."""
    words = len(marker.split())
    if 4 <= words <= 10 or ack_reason:
        return None
    return f"{fid}: marker {marker!r} is {words} word(s), outside the 4-10 word guideline"


def _marker_count(markers: list[str], normalized_text: str) -> int:
    return sum(1 for m in markers if re.search(m, normalized_text))


def site_matches_fact(fact: dict, span_text: str) -> bool:
    normalized = normalize(span_text)
    min_markers = fact.get("min_markers", 1)
    return _marker_count(fact["markers"], normalized) >= min_markers


# --------------------------------------------------------------------------
# Registry validation
# --------------------------------------------------------------------------


def validate_registry(registry: dict) -> None:
    if registry.get("version") != 1:
        raise RegistryError("registry 'version' must be 1")
    facts = registry.get("facts")
    if not isinstance(facts, list):
        raise RegistryError("registry 'facts' must be a list")

    seen_ids: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise RegistryError(f"malformed fact entry: {fact!r}")
        fid = fact.get("id")
        if not fid or not isinstance(fid, str):
            raise RegistryError(f"every fact needs a string 'id', got {fid!r}")
        if fid in seen_ids:
            raise RegistryError(f"duplicate fact id: {fid}")
        seen_ids.add(fid)

        if not fact.get("statement"):
            raise RegistryError(f"{fid}: missing 'statement'")
        if not fact.get("owner"):
            raise RegistryError(f"{fid}: missing 'owner'")
        if fact.get("owner_kind") not in _OWNER_KINDS:
            raise RegistryError(f"{fid}: 'owner_kind' must be one of {sorted(_OWNER_KINDS)}")

        owner_assert = fact.get("owner_assert")
        if not isinstance(owner_assert, dict):
            raise RegistryError(f"{fid}: missing 'owner_assert'")
        if "quote" in owner_assert:
            if not owner_assert["quote"] or not isinstance(owner_assert["quote"], str):
                raise RegistryError(f"{fid}: owner_assert.quote must be a non-empty string")
        elif owner_assert.get("anchor") is True:
            if not owner_assert.get("reason"):
                raise RegistryError(f"{fid}: owner_assert.anchor requires a 'reason'")
        else:
            raise RegistryError(f"{fid}: owner_assert must carry 'quote' or 'anchor'")

        markers = fact.get("markers")
        if not isinstance(markers, list) or not markers:
            raise RegistryError(f"{fid}: needs a non-empty 'markers' list")
        marker_acks = fact.get("marker_acknowledgments", {})
        if not isinstance(marker_acks, dict):
            raise RegistryError(f"{fid}: 'marker_acknowledgments' must be an object")
        for marker in markers:
            if not isinstance(marker, str):
                raise RegistryError(f"{fid}: marker {marker!r} must be a string")
            illegality = _marker_illegality_reason(marker)
            if illegality is not None:
                raise RegistryError(f"{fid}: marker {marker!r} is not legal — {illegality}")
            ack_reason = marker_acks.get(marker)
            if ack_reason is not None and (not isinstance(ack_reason, str) or not ack_reason):
                raise RegistryError(f"{fid}: marker_acknowledgments[{marker!r}] must be a non-empty string")
            warning = _marker_word_count_warning(fid, marker, ack_reason)
            if warning is not None:
                print(f"warning: {warning}", file=sys.stderr)
        for acked_marker in marker_acks:
            if acked_marker not in markers:
                raise RegistryError(
                    f"{fid}: marker_acknowledgments cites {acked_marker!r}, not one of this fact's 'markers'"
                )

        sites = fact.get("sites")
        if not isinstance(sites, list):
            raise RegistryError(f"{fid}: 'sites' must be a list")
        owner_path, has_anchor, owner_symbol = fact["owner"].partition("#")
        for site in sites:
            if not isinstance(site, dict) or not {"file", "symbol", "role"} <= site.keys():
                raise RegistryError(f"{fid}: malformed site entry {site!r}")
            if site["role"] not in _ROLES:
                raise RegistryError(f"{fid}: site role must be one of {sorted(_ROLES)}")
            if (
                site["role"] == "owner"
                and has_anchor
                and not owner_path.startswith("blizzard-context:/")
                and (site["file"], site["symbol"]) != (owner_path, owner_symbol)
            ):
                raise RegistryError(
                    f"{fid}: owner-role site {site['file']}:{site['symbol']} disagrees with the "
                    f"fact's own owner citation {fact['owner']!r}"
                )


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def _site_key(site: dict) -> SiteKey:
    return site["file"], site["symbol"]


def check(
    roots: list[str],
    registry: dict,
    *,
    strict: bool = False,
    owners: bool = False,
    context_root: str | None = None,
) -> tuple[int, list[Finding]]:
    try:
        validate_registry(registry)
    except RegistryError as exc:
        return 2, [Finding("config", "-", str(exc))]

    if owners and context_root is not None and not Path(context_root).is_dir():
        return 2, [Finding("config", "-", f"--context-root {context_root} does not resolve")]

    try:
        all_spans, skipped = collect_spans(roots)
    except RootResolutionError as exc:
        return 2, [Finding("config", "-", str(exc))]
    findings: list[Finding] = []
    exit_code = 0

    for fact in registry["facts"]:
        declared = {_site_key(s): s for s in fact["sites"]}
        owner_keys = {k for k, s in declared.items() if s["role"] == "owner"}
        allowed_keys = {k for k, s in declared.items() if s["role"] != "owner"}

        observed: set[SiteKey] = set()
        for span in all_spans:
            key = (span.file, span.symbol)
            if key in owner_keys:
                continue
            if site_matches_fact(fact, span.text):
                observed.add(key)

        for key in sorted(observed - allowed_keys):
            findings.append(Finding("new", fact["id"], f"{key[0]}:{key[1]}"))
            exit_code = 1
        for key in sorted(allowed_keys - observed):
            findings.append(Finding("stale", fact["id"], f"{key[0]}:{key[1]}"))
            exit_code = 1

        if strict:
            for key in sorted(allowed_keys):
                if not declared[key].get("reason"):
                    findings.append(Finding("strict", fact["id"], f"{key[0]}:{key[1]} carries no reason"))
                    exit_code = 1

        if owners:
            try:
                _check_owner(fact, context_root)
            except OwnerResolutionError as exc:
                findings.append(Finding("owner", fact["id"], str(exc)))
                exit_code = 1

    if skipped:
        findings.append(Finding("skipped", "-", f"{skipped} file(s) unreadable or unparsable, not scanned"))

    return exit_code, findings


def _resolve_owner_path(owner: str, context_root: str | None) -> tuple[Path, str | None]:
    path_part, _, anchor = owner.partition("#")
    anchor = anchor or None
    if path_part.startswith("blizzard-context:/"):
        if context_root is None:
            raise OwnerResolutionError(f"owner {owner!r} cites blizzard-context: but no --context-root was given")
        return Path(context_root) / path_part[len("blizzard-context:/") :], anchor
    return Path(path_part), anchor


def _resolve_owner_scope(owner: str, context_root: str | None) -> str:
    path, anchor = _resolve_owner_path(owner, context_root)
    if not path.is_file():
        raise OwnerResolutionError(f"owner {owner!r} does not resolve — no file at {path}")
    if anchor is None:
        return path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        scope = prose_spans.resolve_md_scope(path, anchor)
        if scope is None:
            raise OwnerResolutionError(f"no heading in {path} slugs to #{anchor}")
        return scope
    scope = prose_spans.resolve_code_scope(path, anchor)
    if scope is None:
        raise OwnerResolutionError(f"{path} yields no prose span attributed to {anchor!r}")
    return scope


def _check_owner(fact: dict, context_root: str | None) -> None:
    scope = _resolve_owner_scope(fact["owner"], context_root)
    owner_assert = fact["owner_assert"]
    if "quote" in owner_assert and normalize(owner_assert["quote"]) not in normalize(scope):
        raise OwnerResolutionError(f"owner_assert.quote not found in {fact['owner']}")


# --------------------------------------------------------------------------
# measure
# --------------------------------------------------------------------------


def measure(roots: list[str], registry: dict) -> tuple[dict[str, set[SiteKey]], int]:
    """Per-fact observed non-owner site inventory over the current tree, plus the
    skipped-file count `--write-sites` refuses to act on blind (see `main`)."""
    all_spans, skipped = collect_spans(roots)
    observed: dict[str, set[SiteKey]] = {}
    for fact in registry["facts"]:
        owner_keys = {_site_key(s) for s in fact["sites"] if s["role"] == "owner"}
        found: set[SiteKey] = set()
        for span in all_spans:
            key = (span.file, span.symbol)
            if key in owner_keys:
                continue
            if site_matches_fact(fact, span.text):
                found.add(key)
        observed[fact["id"]] = found
    return observed, skipped


def apply_write_sites(registry: dict, observed: dict[str, set[SiteKey]]) -> dict:
    """The `--write-sites` preservation contract: an `owner` site is never touched
    or removed; a still-observed declared site keeps its `role`/`reason` verbatim;
    a newly observed site arrives `allowed`/`None`; a declared `allowed` site no
    longer observed is dropped."""
    for fact in registry["facts"]:
        found = observed[fact["id"]]
        declared = {_site_key(s): s for s in fact["sites"]}
        new_sites: list[dict] = []
        for key, site in declared.items():
            if site["role"] == "owner" or key in found:
                new_sites.append(site)
        for key in sorted(found - declared.keys()):
            new_sites.append({"file": key[0], "symbol": key[1], "role": "allowed", "reason": None})
        fact["sites"] = new_sites
    return registry


def render_census_markdown(registry: dict, observed: dict[str, set[SiteKey]]) -> str:
    lines = ["# Restated-invariant census", ""]
    for fact in registry["facts"]:
        lines.append(f"## {fact['id']}")
        lines.append("")
        lines.append(fact["statement"])
        lines.append("")
        lines.append(f"Owner (`{fact['owner_kind']}`): `{fact['owner']}`")
        lines.append("")
        lines.append(f"Observed sites: {len(observed.get(fact['id'], ()))}")
        lines.append("")
    if not registry["facts"]:
        lines.append("_No facts recorded yet._")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------

_CITATION_RE = re.compile(r"blizzard-context:/|winter-canon:/|workspace:/|\b[\w./-]+\.(?:md|py)\b")
_SHINGLE_SIZE = 6
_ISSUE_RE = re.compile(r"#\d+")
_DECISION_RE = re.compile(r"D-\d+")


def discover_pointer(spans: list[ProseSpan]) -> list[ProseSpan]:
    """Spans citing an owner-shaped path/id and carrying more than one line of
    other prose — the highest-precision detector, since these sites have already
    declared an owner."""
    hits = []
    for span in spans:
        text_lines = [ln for ln in span.text.splitlines() if ln.strip()]
        if not any(_CITATION_RE.search(ln) for ln in text_lines):
            continue
        other = [ln for ln in text_lines if not _CITATION_RE.search(ln)]
        if len(other) > 1:
            hits.append(span)
    return hits


def discover_shingle(spans: list[ProseSpan]) -> dict[str, set[str]]:
    """Normalized 6-word shingles occurring in >=2 distinct files."""
    shingle_files: dict[str, set[str]] = {}
    for span in spans:
        words = normalize(span.text).split()
        for i in range(len(words) - _SHINGLE_SIZE + 1):
            shingle = " ".join(words[i : i + _SHINGLE_SIZE])
            shingle_files.setdefault(shingle, set()).add(span.file)
    return {s: files for s, files in shingle_files.items() if len(files) >= 2}


def discover_citation(spans: list[ProseSpan]) -> dict[str, set[str]]:
    """Issue/decision numbers *explained* — not merely cited — in >=2 files."""
    number_files: dict[str, set[str]] = {}
    for span in spans:
        text_lines = [ln for ln in span.text.splitlines() if ln.strip()]
        if len(text_lines) <= 1:
            continue
        for pattern in (_ISSUE_RE, _DECISION_RE):
            for m in pattern.finditer(span.text):
                number_files.setdefault(m.group(0), set()).add(span.file)
    return {n: files for n, files in number_files.items() if len(files) >= 2}


def discover_symbol(spans: list[ProseSpan], names: set[str]) -> dict[str, set[str]]:
    """For each candidate name (a wire field, a Protocol method): the set of files
    whose prose mentions it as a whole word, reported when >=2 files are involved."""
    name_files: dict[str, set[str]] = {}
    for span in spans:
        for name in names:
            if re.search(rf"\b{re.escape(name)}\b", span.text):
                name_files.setdefault(name, set()).add(span.file)
    return {n: files for n, files in name_files.items() if len(files) >= 2}


def _wire_and_protocol_names(roots: list[str]) -> set[str]:
    """Field names declared in `src/blizzard/wire/**`, and method names declared on
    a `Protocol` class anywhere under the scanned roots — the `symbol` detector's
    candidate vocabulary."""
    names: set[str] = set()
    for root in roots:
        for f in Path(root).rglob("*.py"):
            if _is_excluded(f):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            is_wire_file = "wire" in f.parts
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                is_protocol = any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases)
                for item in node.body:
                    if is_wire_file and isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        names.add(item.target.id)
                    if is_protocol and isinstance(item, ast.FunctionDef):
                        names.add(item.name)
    return names


_DETECTORS = ("pointer", "symbol", "shingle", "citation")


def discover(roots: list[str], detector: str | None = None) -> dict[str, object]:
    spans, _skipped = collect_spans(roots)
    results: dict[str, object] = {}
    detectors = [detector] if detector else list(_DETECTORS)
    if "pointer" in detectors:
        results["pointer"] = [f"{s.file}:{s.symbol}" for s in discover_pointer(spans)]
    if "shingle" in detectors:
        results["shingle"] = {k: sorted(v) for k, v in discover_shingle(spans).items()}
    if "citation" in detectors:
        results["citation"] = {k: sorted(v) for k, v in discover_citation(spans).items()}
    if "symbol" in detectors:
        names = _wire_and_protocol_names(roots)
        results["symbol"] = {k: sorted(v) for k, v in discover_symbol(spans, names).items()}
    return results


def render_discover_markdown(candidates: dict[str, object]) -> str:
    lines = ["# Restated-invariant discovery", ""]
    for detector_name, hits in candidates.items():
        lines.append(f"## {detector_name}")
        lines.append("")
        if isinstance(hits, dict):
            for key, files in hits.items():
                lines.append(f"- `{key}` — {', '.join(files)}")
        else:
            for hit in hits:
                lines.append(f"- {hit}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover")
    p_discover.add_argument("roots", nargs="+")
    p_discover.add_argument("--detector", choices=_DETECTORS)
    p_discover.add_argument("--json", action="store_true")
    p_discover.add_argument("--markdown", action="store_true")

    p_measure = sub.add_parser("measure")
    p_measure.add_argument("roots", nargs="+")
    p_measure.add_argument("--write-sites", action="store_true")
    p_measure.add_argument("--force", action="store_true")
    p_measure.add_argument("--markdown", action="store_true")

    p_check = sub.add_parser("check")
    p_check.add_argument("roots", nargs="+")
    p_check.add_argument("--strict", action="store_true")
    p_check.add_argument("--owners", action="store_true")
    p_check.add_argument("--context-root")

    args = parser.parse_args(argv)

    if args.command == "check":
        registry = _load_registry()
        exit_code, findings = check(
            args.roots, registry, strict=args.strict, owners=args.owners, context_root=args.context_root
        )
        for finding in findings:
            print(finding)
        if exit_code == 0:
            print("restatement sweep: clean")
        return exit_code

    if args.command == "measure":
        registry = _load_registry()
        try:
            observed, skipped = measure(args.roots, registry)
        except RootResolutionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.write_sites:
            if skipped and not args.force:
                print(
                    f"error: {skipped} file(s) were skipped (unreadable or unparsable) — "
                    "--write-sites refuses to rewrite sites[] against a partial scan, since a "
                    "skipped restating site would read back as removed. Fix the skip, or pass "
                    "--force to write anyway.",
                    file=sys.stderr,
                )
                return 2
            if skipped and args.force:
                print(
                    f"warning: writing sites with {skipped} file(s) skipped (--force) — "
                    "sites[] may silently drop a declared site the skip hid from this scan.",
                    file=sys.stderr,
                )
            registry = apply_write_sites(registry, observed)
            REGISTRY.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"sites written: {REGISTRY}")
        if args.markdown:
            print(render_census_markdown(registry, observed))
        else:
            for fact_id, sites in observed.items():
                print(f"{fact_id}: {len(sites)} site(s)")
        return 0

    if args.command == "discover":
        try:
            candidates = discover(args.roots, detector=args.detector)
        except RootResolutionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.markdown:
            print(render_discover_markdown(candidates))
        elif args.json:
            print(json.dumps(candidates, indent=2))
        else:
            for detector_name, hits in candidates.items():
                print(f"{detector_name}: {len(hits)} candidate(s)")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

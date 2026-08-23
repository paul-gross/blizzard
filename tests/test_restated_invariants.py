"""Unit-tier coverage of `scripts/prose_spans.py` and `scripts/restated_invariants.py`
(issue paul-gross/blizzard#273), plus validation of the committed registry
(`scripts/restated-invariants.json`) — case numbers are this file's own, in
authoring order. The registry's own triage narrative lives on the GitHub issue at
delivery, not in this repo.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"


@contextlib.contextmanager
def _cwd(path: Path):
    """`ri.check`/`ri.measure` take roots relative to the caller's cwd — the same
    contract the real `mise run restatement-check` invocation relies on — so a
    fixture tree under `tmp_path` is scanned by chdir-ing into it and passing `"."`,
    keeping every declared `file` in a fixture registry a plain relative name."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _load_module(name: str, filename: str) -> ModuleType:
    """`scripts/` is not an importable package — no `__init__.py`, not on `sys.path`.
    Registering in `sys.modules` before `exec_module` is what lets `restated_invariants`'s
    bare `import prose_spans` resolve, with no `sys.path` mutation."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prose_spans = _load_module("prose_spans", "prose_spans.py")
ri = _load_module("restated_invariants", "restated_invariants.py")


def _fact(
    fact_id: str = "fact-1",
    *,
    markers: list[str] | None = None,
    sites: list[dict] | None = None,
    owner: str = "src/owner.py#Owner",
    owner_kind: str = "module",
    quote: str = "the owner states it",
    min_markers: int | None = None,
) -> dict:
    fact = {
        "id": fact_id,
        "statement": "A fact stated for testing.",
        "owner": owner,
        "owner_kind": owner_kind,
        "owner_assert": {"quote": quote},
        "markers": markers if markers is not None else ["a marker phrase"],
        "sites": sites if sites is not None else [],
    }
    if min_markers is not None:
        fact["min_markers"] = min_markers
    return fact


def _registry(facts: list[dict]) -> dict:
    return {"version": 1, "facts": facts}


# Case 1 — a marker matching across a line break inside a `/** */` block is found.


def test_case1_marker_matches_across_line_break_in_ts_block(tmp_path: Path) -> None:
    ts = tmp_path / "widget.ts"
    ts.write_text("/**\n * The verb is wrapped\n * implies raw, never the reverse.\n */\nexport class Widget {}\n")
    fact = _fact(markers=["wrapped implies raw"])
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 1
    assert any(f.kind == "new" and f.detail == "widget.ts:Widget" for f in findings)


# Case 2 — a path under an excluded root is not reported even though it carries
# the marker.


def test_case2_excluded_root_not_reported(tmp_path: Path) -> None:
    excluded = tmp_path / "openapi"
    excluded.mkdir()
    (excluded / "widget.ts").write_text(
        "/**\n * The verb is wrapped\n * implies raw, never the reverse.\n */\nexport class Widget {}\n"
    )
    fact = _fact(markers=["wrapped implies raw"])
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 0
    assert findings == []


# Case 3 — a site citing the owner but carrying no marker is not a finding.


def test_case3_pointer_with_no_marker_is_not_a_finding(tmp_path: Path) -> None:
    py = tmp_path / "pointer.py"
    py.write_text('"""See src/owner.py#Owner for the rule."""\n')
    fact = _fact(markers=["a marker phrase never appearing here"])
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 0
    assert findings == []


# Case 4 — a site citing the owner and carrying a marker is a finding.


def test_case4_pointer_plus_precis_is_a_finding(tmp_path: Path) -> None:
    py = tmp_path / "precis.py"
    py.write_text('"""A marker phrase — see src/owner.py#Owner."""\n')
    fact = _fact(markers=["a marker phrase"])
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 1
    assert any(f.kind == "new" and f.detail == "precis.py:<module>" for f in findings)


# Case 5 — a single-token marker is rejected with exit 2.


def test_case5_single_token_marker_rejected(tmp_path: Path) -> None:
    fact = _fact(markers=["takeover_command"])
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 2
    assert findings[0].kind == "config"


# Case 6 — a declared `allowed` site no longer carrying its marker is `stale`.


def test_case6_stale_declared_site(tmp_path: Path) -> None:
    py = tmp_path / "site.py"
    py.write_text('"""Nothing relevant here."""\n')
    fact = _fact(
        markers=["a marker phrase"],
        sites=[{"file": "site.py", "symbol": "<module>", "role": "allowed", "reason": "was reduced elsewhere"}],
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 1
    assert any(f.kind == "stale" and f.detail == "site.py:<module>" for f in findings)


# Case 7 — an `owner`-role site is exempt both ways.


def test_case7_owner_role_exempt_when_marker_absent(tmp_path: Path) -> None:
    py = tmp_path / "owner.py"
    py.write_text('"""Nothing matching the marker here."""\n')
    fact = _fact(
        owner="owner.py#<module>",
        markers=["a marker phrase"],
        sites=[{"file": "owner.py", "symbol": "<module>", "role": "owner"}],
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 0
    assert findings == []


def test_case7_owner_role_exempt_when_marker_present(tmp_path: Path) -> None:
    py = tmp_path / "owner.py"
    py.write_text('"""States a marker phrase in its own words."""\n')
    fact = _fact(
        owner="owner.py#<module>",
        markers=["a marker phrase"],
        sites=[{"file": "owner.py", "symbol": "<module>", "role": "owner"}],
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 0
    assert findings == []


# Case 8 — `--owners` fails on a quote absent from the resolved scope, and passes
# when the owner's wording shares no marker (the domain-owner regression).


def test_case8_owners_fails_on_missing_quote(tmp_path: Path) -> None:
    owner_md = tmp_path / "humans.md"
    owner_md.write_text("# Domain\n\n## Escalation\n\nSome unrelated sentence.\n")
    fact = _fact(
        owner=f"{owner_md}#escalation",
        owner_kind="domain",
        quote="always means the raw resume command is present too",
        markers=["wrapped implies raw"],
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]), owners=True)
    assert exit_code == 1
    assert any(f.kind == "owner" for f in findings)


def test_case8_owners_passes_with_different_wording_than_markers(tmp_path: Path) -> None:
    owner_md = tmp_path / "humans.md"
    owner_md.write_text(
        "# Domain\n\n"
        "## Escalation\n\n"
        "A present wrapped takeover verb always means the raw resume command is "
        "present too, but not the other way around.\n"
    )
    fact = _fact(
        owner=f"{owner_md}#escalation",
        owner_kind="domain",
        quote="always means the raw resume command is present too, but not the other way around",
        markers=["wrapped implies raw"],  # a phrase the owner's own wording never uses
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]), owners=True)
    assert exit_code == 0
    assert findings == []


# Case 9 — `--owners` with an unresolvable `--context-root` exits non-zero.


def test_case9_unresolvable_context_root_exits_nonzero(tmp_path: Path) -> None:
    fact = _fact(owner="blizzard-context:/domain/humans.md#escalation", owner_kind="domain")
    exit_code, findings = ri.check(
        [str(tmp_path)], _registry([fact]), owners=True, context_root=str(tmp_path / "does-not-exist")
    )
    assert exit_code == 2
    assert findings[0].kind == "config"


# Case 10 — `--strict` fails on an `allowed` site with no `reason`; passes with one.


def test_case10_strict_fails_without_reason(tmp_path: Path) -> None:
    py = tmp_path / "site.py"
    py.write_text('"""A marker phrase, restated here."""\n')
    fact = _fact(
        markers=["a marker phrase"],
        sites=[{"file": "site.py", "symbol": "<module>", "role": "allowed", "reason": None}],
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]), strict=True)
    assert exit_code == 1
    assert any(f.kind == "strict" for f in findings)


def test_case10_strict_passes_with_reason(tmp_path: Path) -> None:
    py = tmp_path / "site.py"
    py.write_text('"""A marker phrase, restated here."""\n')
    fact = _fact(
        markers=["a marker phrase"],
        sites=[{"file": "site.py", "symbol": "<module>", "role": "allowed", "reason": "structural survivor"}],
    )
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]), strict=True)
    assert exit_code == 0
    assert findings == []


# Case 11 — site identity is stable: inserting unrelated lines above a span does
# not change its `symbol`.


def test_case11_symbol_stable_across_unrelated_insertions(tmp_path: Path) -> None:
    before = tmp_path / "before.py"
    before.write_text('def widget():\n    """Docstring on widget."""\n    return 1\n')
    after = tmp_path / "after.py"
    after.write_text(
        "import os\n\n\ndef other():\n    return os.getpid()\n\n\n"
        'def widget():\n    """Docstring on widget."""\n    return 1\n'
    )

    def _symbol_for(path: Path) -> str:
        spans = prose_spans.extract_spans(path)
        (span,) = [s for s in spans if s.symbol == "widget"]
        return span.symbol

    assert _symbol_for(before) == _symbol_for(after) == "widget"


# Case 12 — the committed `scripts/restated-invariants.json`.


def _load_committed_registry() -> dict:
    return ri._load_registry()


def test_case12a_committed_registry_schema() -> None:
    registry = _load_committed_registry()
    ri.validate_registry(registry)  # raises RegistryError on any schema violation


def test_case12b_committed_registry_in_repo_agreement() -> None:
    registry = _load_committed_registry()
    for fact in registry["facts"]:
        for site in fact["sites"]:
            if site["file"].startswith("../"):
                continue
            path = _REPO_ROOT / site["file"]
            assert path.is_file(), f"{fact['id']}: declared site {site['file']} does not exist"
            spans = prose_spans.extract_spans(path)
            assert any(s.symbol == site["symbol"] for s in spans), (
                f"{fact['id']}: {site['file']} yields no span attributed to {site['symbol']!r}"
            )
        owner_path = fact["owner"].split("#", 1)[0]
        if owner_path.startswith("blizzard-context:/"):
            continue
        with _cwd(_REPO_ROOT):
            ri._check_owner(fact, context_root=None)


def test_case12c_committed_registry_cross_repo_agreement() -> None:
    mock_root = _REPO_ROOT.parent / "blizzard-mock"
    context_root = _REPO_ROOT.parent / "blizzard-context"
    if not mock_root.is_dir() or not context_root.is_dir():
        pytest.skip(
            "sibling blizzard-mock/blizzard-context worktrees absent — cross-repo site/owner "
            "agreement unchecked here; covered by the sweep's own feature-env `--owners` runs "
            "(Phases 2, 4, 6)"
        )
    registry = _load_committed_registry()
    for fact in registry["facts"]:
        for site in fact["sites"]:
            if not site["file"].startswith("../"):
                continue
            path = _REPO_ROOT / site["file"]
            assert path.is_file(), f"{fact['id']}: declared site {site['file']} does not exist"
            spans = prose_spans.extract_spans(path)
            assert any(s.symbol == site["symbol"] for s in spans)
        if fact["owner"].startswith("blizzard-context:/"):
            ri._resolve_owner_scope(fact["owner"], context_root=str(context_root))


# Case 13 — site identity is correct against real files.


def test_case13_chunk_escalation_leading_block_attributes_to_class() -> None:
    path = _REPO_ROOT / "web/projects/fleet/src/lib/chunk-detail/chunk-escalation.ts"
    spans = prose_spans.extract_spans(path)
    leading = [s for s in spans if s.start_line == 6]
    assert len(leading) == 1
    assert leading[0].symbol == "ChunkEscalation"


def test_case13_deployment_doc_heading_slug() -> None:
    path = _REPO_ROOT / "docs/deployment/chunk-operations/takeover.md"
    expected_slug = "taking-over-a-parked-session"
    spans = prose_spans.extract_spans(path)
    matching = [s for s in spans if s.symbol == expected_slug]
    assert matching, f"no span attributed to {expected_slug!r}"


# Case 14 — `--write-sites` is non-destructive.


def test_case14_write_sites_preservation_contract(tmp_path: Path) -> None:
    still_observed = tmp_path / "still.py"
    still_observed.write_text('"""A marker phrase, still here."""\n')
    owner_site = tmp_path / "owner.py"
    owner_site.write_text('"""Nothing matching."""\n')
    newly_observed = tmp_path / "new.py"
    newly_observed.write_text('"""A marker phrase, newly restated."""\n')
    # "gone.py" — declared allowed but no longer observed — is deliberately absent.

    fact = _fact(
        markers=["a marker phrase"],
        sites=[
            {"file": "still.py", "symbol": "<module>", "role": "allowed", "reason": "kept for now"},
            {"file": "owner.py", "symbol": "<module>", "role": "owner"},
            {"file": "gone.py", "symbol": "<module>", "role": "allowed", "reason": None},
        ],
    )
    registry = _registry([fact])
    with _cwd(tmp_path):
        observed, skipped = ri.measure(["."], registry)
    assert skipped == 0
    updated = ri.apply_write_sites(registry, observed)

    sites_by_key = {(s["file"], s["symbol"]): s for s in updated["facts"][0]["sites"]}
    assert sites_by_key[("still.py", "<module>")]["role"] == "allowed"
    assert sites_by_key[("still.py", "<module>")]["reason"] == "kept for now"
    assert sites_by_key[("owner.py", "<module>")]["role"] == "owner"
    assert ("gone.py", "<module>") not in sites_by_key
    assert sites_by_key[("new.py", "<module>")] == {
        "file": "new.py",
        "symbol": "<module>",
        "role": "allowed",
        "reason": None,
    }


# Case 15 — attribution precedence.


def test_case15_py_module_docstring_stays_module_with_imports_only(tmp_path: Path) -> None:
    one_class = tmp_path / "one_class.py"
    one_class.write_text('"""Module docstring."""\n\nimport os\n\n\nclass First:\n    pass\n')
    two_classes = tmp_path / "two_classes.py"
    two_classes.write_text(
        '"""Module docstring."""\n\nimport os\n\n\nclass Zeroth:\n    pass\n\n\nclass First:\n    pass\n'
    )

    def _module_docstring_symbol(path: Path) -> str:
        spans = prose_spans.extract_spans(path)
        (span,) = [s for s in spans if s.start_line == 1]
        return span.symbol

    assert _module_docstring_symbol(one_class) == "<module>"
    assert _module_docstring_symbol(two_classes) == "<module>"


def test_case15_ts_block_above_import_is_module(tmp_path: Path) -> None:
    ts = tmp_path / "header.ts"
    ts.write_text("/** File header. */\nimport { Foo } from './foo';\n\nexport class Bar {}\n")
    spans = prose_spans.extract_spans(ts)
    (span,) = spans
    assert span.symbol == "<module>"


def test_case15_ts_block_above_decorated_class_is_the_class(tmp_path: Path) -> None:
    ts = tmp_path / "component.ts"
    ts.write_text("/** A component. */\n@Component({\n  selector: 'app-foo',\n})\nexport class Foo {}\n")
    spans = prose_spans.extract_spans(ts)
    (span,) = spans
    assert span.symbol == "Foo"


# An unresolvable root refuses a green rather than being silently skipped —
# the same discipline `--context-root` already gets.


def test_unresolvable_root_fails_check_rather_than_reporting_clean(tmp_path: Path) -> None:
    fact = _fact()
    exit_code, findings = ri.check([str(tmp_path / "does-not-exist")], _registry([fact]))
    assert exit_code == 2
    assert findings[0].kind == "config"


def test_unresolvable_root_raises_for_measure_and_discover(tmp_path: Path) -> None:
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(ri.RootResolutionError):
        ri.measure([missing], _registry([_fact()]))
    with pytest.raises(ri.RootResolutionError):
        ri.discover([missing])


def test_unresolvable_root_exits_nonzero_via_cli(tmp_path: Path) -> None:
    missing = str(tmp_path / "does-not-exist")
    assert ri.main(["check", missing]) == 2
    assert ri.main(["measure", missing]) == 2
    assert ri.main(["discover", missing]) == 2


# A single-file root — `blizzard/README.md`, the one bound surface that is not a
# tree (blizzard#274). `rglob` on a file yields nothing, so a root that resolved
# but swept zero files would report permanently clean.


def test_a_single_file_root_is_actually_swept(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# T\n\nprose\n")
    assert ri.iter_files([str(readme)]) == [readme]
    spans, skipped = ri.collect_spans([str(readme)])
    assert spans and skipped == 0


def test_a_file_root_that_is_itself_excluded_is_refused(tmp_path: Path) -> None:
    # An excluded file root resolves and then sweeps nothing — a permanent green,
    # which is exactly what RootResolutionError exists to refuse.
    generated = tmp_path / "openapi" / "hub.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("# generated\n")
    with pytest.raises(ri.RootResolutionError):
        ri.iter_files([str(generated)])


def test_a_file_root_of_an_unswept_extension_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "mise.toml"
    other.write_text("[tasks]\n")
    with pytest.raises(ri.RootResolutionError):
        ri.iter_files([str(other)])


def test_a_file_root_reports_a_restatement_the_registry_does_not_declare(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# T\n\nThis file carries a marker phrase of its own.\n")
    exit_code, findings = ri.check([str(readme)], _registry([_fact()]))
    assert exit_code == 1
    assert any(f.kind == "new" for f in findings)


# Case 16 — a `.py` file that fails to parse yields no spans rather than raising
# (the `tokenize.TokenizeError` typo — the real name is `TokenError`).


def test_case16_unparsable_python_file_yields_no_spans(tmp_path: Path) -> None:
    py = tmp_path / "broken.py"
    py.write_text("def broken(:\n    pass\n")
    assert prose_spans.extract_spans(py) == []


# Case 17 — a skipped file (unreadable, or unparsable) is surfaced as a finding
# beside the clean verdict, not silently dropped.


def test_case17_check_surfaces_skipped_file_count(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n")
    fact = _fact()
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 0
    assert any(f.kind == "skipped" and "1 file" in f.detail for f in findings)


# Case 18 — a marker that does not normalize to itself (capitals, punctuation) is
# rejected with exit 2, the same config path as a single-token marker (case 5).


def test_case18_marker_not_normalized_to_itself_rejected(tmp_path: Path) -> None:
    fact = _fact(markers=["Type Tag, Underscore"])
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 2
    assert findings[0].kind == "config"


# Case 19 — the 4-10 word marker guideline is a warning, not a rejection.


def test_case19_short_marker_warns_but_does_not_reject(capsys: pytest.CaptureFixture[str]) -> None:
    registry = _registry([_fact(markers=["a marker phrase"])])  # 3 words
    ri.validate_registry(registry)  # does not raise
    assert "outside the 4-10 word guideline" in capsys.readouterr().err


def test_case19_marker_within_range_does_not_warn(capsys: pytest.CaptureFixture[str]) -> None:
    registry = _registry([_fact(markers=["a legal marker phrase here"])])  # 5 words
    ri.validate_registry(registry)
    assert capsys.readouterr().err == ""


# Case 20 — `min_markers` requires a conjunction: a site carrying only one of two
# required markers is not a finding, carrying both is.


def test_case20_min_markers_requires_conjunction(tmp_path: Path) -> None:
    py = tmp_path / "site.py"
    fact = _fact(markers=["phrase one here", "phrase two here"], min_markers=2)

    py.write_text('"""Carries phrase one here but not the other."""\n')
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 0
    assert findings == []

    py.write_text('"""Carries phrase one here and phrase two here both."""\n')
    with _cwd(tmp_path):
        exit_code, findings = ri.check(["."], _registry([fact]))
    assert exit_code == 1
    assert any(f.kind == "new" for f in findings)


# Case 21 — an `owner`-role site whose (file, symbol) disagrees with the fact's
# own owner citation is a schema violation, not a silently accepted mismatch.


def test_case21_owner_role_site_must_agree_with_owner_citation() -> None:
    fact = _fact(
        owner="src/owner.py#Owner",
        sites=[{"file": "src/owner.py", "symbol": "SomethingElse", "role": "owner"}],
    )
    with pytest.raises(ri.RegistryError):
        ri.validate_registry(_registry([fact]))


# Case 22 — `measure --write-sites` refuses to rewrite `sites[]` when a file was
# skipped, unless `--force` is given.


def test_case22_write_sites_refuses_on_skip_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "site.py").write_text('"""A marker phrase, here."""\n')
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry([_fact(markers=["a marker phrase"])])))
    monkeypatch.setattr(ri, "REGISTRY", registry_path)
    with _cwd(tmp_path):
        exit_code = ri.main(["measure", ".", "--write-sites"])
    assert exit_code == 2
    assert "skipped" in capsys.readouterr().err
    assert json.loads(registry_path.read_text())["facts"][0]["sites"] == []


def test_case22_write_sites_force_overrides_the_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "site.py").write_text('"""A marker phrase, here."""\n')
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry([_fact(markers=["a marker phrase"])])))
    monkeypatch.setattr(ri, "REGISTRY", registry_path)
    with _cwd(tmp_path):
        exit_code = ri.main(["measure", ".", "--write-sites", "--force"])
    assert exit_code == 0
    assert "skipped" in capsys.readouterr().err
    written = json.loads(registry_path.read_text())["facts"][0]["sites"]
    assert [s["file"] for s in written] == ["site.py"]


# Case 23 — an RST double-backtick span is stripped the same as a single-backtick one.


def test_case23_normalize_strips_double_backtick_spans_too() -> None:
    assert ri.normalize("See ``ChunkIngestRequest`` for the shape.") == ri.normalize(
        "See `ChunkIngestRequest` for the shape."
    )
    assert "chunkingestrequest" not in ri.normalize("See ``ChunkIngestRequest`` for the shape.")

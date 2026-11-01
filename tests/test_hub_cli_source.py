"""``blizzard hub source`` and ``blizzard hub config changes`` (component tier) — a real
``build_hosted_app`` the commands' requests are routed into, headers included, so the
recorded door is the CLI's own."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.cli import hub as hub_group
from tests.config_inputs import build_hosted_hub_app, init_hub
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB = "http://hub.local:8421"
_SENTINEL = "tok-planted-840"


_bound: dict[str, Bound] = {}
"""The collaborators the ``client`` fixture bound over its in-process app, for the invokers below."""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = init_hub(tmp_path / "hub")
    with TestClient(build_hosted_hub_app(config)) as c:

        def forward(method: str, url: str, *, timeout: float, **kwargs):  #
            return c.request(method.upper(), urlsplit(url).path, **kwargs)

        _bound["hub"] = bind_stubs(request=forward)
        yield c


def _cli(group: str, args: list[str], *, stdin: str | None = None):  #
    """Run one ``hub`` verb. Its ``output`` interleaves stdout with stderr, where the hosted app's sweeps log
    while the verb runs, so a ``--json`` payload is parsed from ``stdout`` alone."""
    return _bound["hub"].runner.invoke(hub_group, [group, *args], input=stdin, env={"BZ_HUB_URL": _HUB})


def _source(args: list[str]):  #
    return _cli("source", args)


def _seed(client: TestClient) -> None:
    assert _cli("secret", ["create", "gh-demo"], stdin=_SENTINEL).exit_code == 0
    created = _source(["create", "demo", "--provider", "github", "--locator", "acme/demo", "--secret", "gh-demo"])
    assert created.exit_code == 0, created.output


def test_create_list_and_show_with_and_without_json(client: TestClient) -> None:
    _seed(client)

    listed = _source(["list"])
    assert "hub  built-in" in listed.output
    rows = json.loads(_source(["list", "--json"]).stdout)
    assert {r["slug"] for r in rows} == {"hub", "demo"}
    source_id = next(r["work_source_id"] for r in rows if r["slug"] == "demo")
    assert f"demo  demo  {source_id}  r1  enabled  github  acme/demo" in listed.output
    shown = _source(["get", "demo"])
    assert "demo  demo  revision 1" in shown.output
    assert f"id {source_id}" in shown.output
    assert "secret gh-demo" in shown.output
    assert json.loads(_source(["get", "demo", "--json"]).stdout)["revision"] == 1
    assert json.loads(_source(["get", source_id, "--json"]).stdout)["slug"] == "demo"
    assert _source(["get", "nope"]).exit_code != 0


def test_create_takes_a_slug_and_a_name_and_edit_renames(client: TestClient) -> None:
    assert _cli("secret", ["create", "gh-demo"], stdin=_SENTINEL).exit_code == 0
    created = _source(
        ["create", "acme", "--name", "Acme", "--provider", "github", "--locator", "acme/a", "--secret", "gh-demo"]
    )
    assert created.exit_code == 0, created.output
    assert "work source acme created at revision 1 — id wsrc_" in created.output
    renamed = _source(["edit", "acme", "--slug", "acme-core", "--name", "Acme core"])
    assert renamed.exit_code == 0, renamed.output
    assert "work source acme-core is at revision 2" in renamed.output
    assert "acme-core  Acme core  revision 2" in _source(["get", "acme"]).output
    assert _source(["edit", "acme-core", "--slug", "hub"]).exit_code != 0
    refused = _source(["create", "hub", "--provider", "github", "--locator", "acme/h", "--secret", "gh-demo"])
    assert refused.exit_code != 0


def test_edit_sets_clears_and_refuses_a_stale_if_match(client: TestClient) -> None:
    _seed(client)

    assert "revision 2" in _source(["edit", "demo", "--api-base", "https://ghe.example/api/v3"]).output
    assert "revision 3" in _source(["edit", "demo", "--clear", "api-base"]).output
    assert json.loads(_source(["get", "demo", "--json"]).stdout)["api_base"] == "https://api.github.com"
    stale = _source(["edit", "demo", "--if-match", "1", "--no-annotate"])
    assert stale.exit_code != 0
    assert "revision 3" in stale.output
    assert _source(["edit", "demo"]).exit_code != 0
    assert _source(["edit", "demo", "--api-base", "x", "--clear", "api-base"]).exit_code != 0
    refused = _source(["edit", "demo", "--clear", "secret"])
    assert refused.exit_code != 0
    assert "secret" in refused.output
    assert _source(["edit", "hub", "--annotate"]).exit_code != 0


def test_retire_and_enable_and_the_secret_reference_guard(client: TestClient) -> None:
    _seed(client)

    refused = _cli("secret", ["retire", "gh-demo"])
    assert refused.exit_code != 0
    assert "work_source demo" in refused.output
    assert "work_source demo" in _cli("secret", ["get", "gh-demo"]).output
    source_id = json.loads(_source(["get", "demo", "--json"]).stdout)["work_source_id"]
    assert json.loads(_cli("secret", ["get", "gh-demo", "--json"]).stdout)["references"] == [
        {"kind": "work_source", "key": source_id, "label": "demo"}
    ]

    assert "retired" in _source(["retire", "demo"]).output
    assert "demo" not in _source(["list"]).output
    assert "demo" in _source(["list", "--include-retired"]).output
    clash = _source(["create", "demo2", "--provider", "github", "--locator", "acme/demo", "--secret", "gh-demo"])
    assert clash.exit_code != 0
    assert "demo" in clash.output
    assert "enabled" in _source(["enable", "demo"]).output


def test_config_changes_carry_the_cli_door_and_no_secret_value(client: TestClient) -> None:
    _seed(client)
    client.patch("/api/work-sources/demo", json={"annotate": True})

    text = _cli("config", ["changes"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert "api  work_source demo  r2  edit  annotate" in lines[0]
    assert "cli  work_source demo  r1  create" in lines[1]
    assert "cli  forge github  r1  create" in lines[2]
    assert "cli  secret gh-demo  r1  create" in lines[3]
    assert _SENTINEL not in text.output

    raw = json.loads(_cli("config", ["changes", "--json", "--kind", "secret", "--all"]).stdout)
    assert [(c["door"], c["record_key"]) for c in raw["changes"]] == [("cli", "gh-demo")]
    assert _SENTINEL not in json.dumps(raw)
    assert "no configuration changes" in _cli("config", ["changes", "--key", "absent"]).output


# --- config apply and export --------------------------------------------------------

_APPLY_YAML = """\
version: 1
secrets: [gh-demo]
work_sources:
  - {slug: demo, provider: github, locator: acme/demo, secret: gh-demo}
repositories:
  - {slug: blizzard, forge_api_url: "https://api.github.com", owner: acme, repo: blizzard, base_branch: master, secret_name: gh-demo}
"""


def _config(args: list[str]):  #
    return _cli("config", args)


def _lines(output: str, op: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(op + " ")]


def _changes_count() -> int:
    return len(json.loads(_config(["changes", "--all", "--json"]).stdout)["changes"])


def test_yaml_yml_and_json_files_apply(client: TestClient, tmp_path: Path) -> None:
    assert _cli("secret", ["create", "gh-demo"], stdin=_SENTINEL).exit_code == 0
    as_json = {
        "version": 1,
        "work_sources": [{"slug": "from-json", "provider": "github", "locator": "acme/j", "secret": "gh-demo"}],
    }
    (tmp_path / "a.yaml").write_text(_APPLY_YAML)
    (tmp_path / "b.json").write_text(json.dumps(as_json))
    (tmp_path / "c.yml").write_text(_APPLY_YAML)
    first = _config(["apply", str(tmp_path / "a.yaml")])
    assert first.exit_code == 0, first.output
    assert len(_lines(first.output, "create")) == 3
    from_json = _config(["apply", str(tmp_path / "b.json")])
    assert from_json.exit_code == 0, from_json.output
    assert len(_lines(from_json.output, "create")) == 1
    from_yml = _config(["apply", str(tmp_path / "c.yml")])
    assert from_yml.exit_code == 0, from_yml.output
    assert len(_lines(from_yml.output, "unchanged")) == 2


def test_an_unknown_extension_is_refused_before_any_request(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(_APPLY_YAML)
    before = _changes_count()
    result = _config(["apply", str(path)])
    assert result.exit_code != 0
    assert ".yaml" in result.output and ".json" in result.output
    assert _changes_count() == before


def test_a_dry_run_prints_the_outcome_and_writes_nothing(client: TestClient, tmp_path: Path) -> None:
    assert _cli("secret", ["create", "gh-demo"], stdin=_SENTINEL).exit_code == 0
    path = tmp_path / "cfg.yaml"
    path.write_text(_APPLY_YAML)
    before = _changes_count()
    dry = _config(["apply", str(path), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert len(_lines(dry.output, "create")) == 3
    assert _changes_count() == before
    real = _config(["apply", str(path)])
    assert _lines(real.output, "create") == _lines(dry.output, "create")
    rows = json.loads(_config(["changes", "--json"]).stdout)["changes"][:2]
    assert {r["door"] for r in rows} == {"apply"}
    assert len({r["apply_id"] for r in rows}) == 1


def test_an_exported_document_applies_back_as_all_unchanged(client: TestClient, tmp_path: Path) -> None:
    assert _cli("secret", ["create", "gh-demo"], stdin=_SENTINEL).exit_code == 0
    path = tmp_path / "cfg.yaml"
    path.write_text(_APPLY_YAML)
    assert _config(["apply", str(path)]).exit_code == 0
    before = _changes_count()
    for fmt, suffix in (("yaml", "yaml"), ("json", "json")):
        exported = _config(["export", "--format", fmt])
        assert exported.exit_code == 0, exported.output
        out = tmp_path / f"out.{suffix}"
        out.write_text(exported.stdout)
        replay = _config(["apply", str(out)])
        assert replay.exit_code == 0, replay.output
        unchanged = _lines(replay.output, "unchanged")
        assert len(unchanged) == 4 and not _lines(replay.output, "create")
        assert any(line.split()[1:] == ["project", "default"] for line in unchanged)
        assert any(line.split()[1:] == ["forge", "github"] for line in unchanged)
    assert json.loads(_config(["export", "--format", "json"]).stdout)["version"] == 2
    assert _changes_count() == before


def test_a_refused_apply_reports_the_hub_s_refusal(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "cfg.yaml"
    path.write_text(_APPLY_YAML)
    result = _config(["apply", str(path)])
    assert result.exit_code != 0
    assert "secrets.0: secret gh-demo is unknown" in result.output


def test_forge_names_the_source_s_forge_and_the_old_flags_warn(client: TestClient) -> None:
    _seed(client)
    moved = _source(["edit", "demo", "--api-base", "https://ghe.example/api/v3"])
    assert moved.exit_code == 0, moved.output
    assert "warning: api_base is deprecated; it resolved to forge ghe-example" in moved.stderr
    assert "forge ghe-example" in _source(["get", "demo"]).output

    back = _source(["edit", "demo", "--forge", "github"])
    assert back.exit_code == 0, back.output
    assert "warning:" not in back.stderr
    assert json.loads(_source(["get", "demo", "--json"]).stdout)["forge_slug"] == "github"

    created = _source(
        [
            "create",
            "other",
            "--provider",
            "github",
            "--locator",
            "acme/o",
            "--secret",
            "gh-demo",
            "--forge",
            "ghe-example",
        ]
    )
    assert created.exit_code == 0, created.output
    assert "warning:" not in created.stderr
    assert json.loads(_source(["get", "other", "--json"]).stdout)["api_base"] == "https://ghe.example/api/v3"

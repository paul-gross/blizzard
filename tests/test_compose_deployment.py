"""The reference compose deployment (``packaging/docker/``) — the static packaging
contract (issue #191), the same docker-free-guard shape as
``tests/test_container_image.py`` / ``tests/test_systemd_units.py``. No docker
required. The stack actually standing up, serving through the proxy, and
surviving a restart is ``blizzard:compose-smoke`` (``mise run compose-smoke``),
local-only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCKER_DIR = _REPO_ROOT / "packaging" / "docker"


def _compose() -> dict:
    return yaml.safe_load((_DOCKER_DIR / "compose.yaml").read_text())


def _hub_config_toml() -> dict:
    return tomllib.loads((_DOCKER_DIR / "blizzard-hub.toml").read_text())


def test_compose_and_companion_files_exist() -> None:
    for name in ("compose.yaml", "Caddyfile", "blizzard-hub.toml", ".env.example", "README.md"):
        assert (_DOCKER_DIR / name).is_file(), f"packaging/docker/{name} is missing"


def test_three_services_declared() -> None:
    services = _compose()["services"]
    assert set(services) == {"postgres", "hub", "caddy"}


def test_every_durable_path_is_a_named_volume() -> None:
    """A named volume, never a host bind-mount, for every path holding state that
    must survive `docker compose down` (no `-v`) + `up` — a host bind silently
    drops on a different host/CI runner, and #188's non-root image can't even
    write to a freshly-created host directory (a real failure this test's Dockerfile
    sibling smoke hit during development)."""
    compose = _compose()
    top_level_volumes = set(compose.get("volumes") or {})
    assert {"postgres-data", "hub-data", "caddy-data", "caddy-config"} <= top_level_volumes

    services = compose["services"]
    durable_mounts = {
        "postgres": "postgres-data",
        "hub": "hub-data",
    }
    for service, volume in durable_mounts.items():
        mounts = services[service].get("volumes", [])
        named_volume_mounts = [m for m in mounts if isinstance(m, str) and m.startswith(f"{volume}:")]
        assert named_volume_mounts, f"{service} must mount the named volume {volume!r} for its durable state"

    caddy_mounts = services["caddy"].get("volumes", [])
    for volume in ("caddy-data", "caddy-config"):
        assert any(isinstance(m, str) and m.startswith(f"{volume}:") for m in caddy_mounts), (
            f"caddy must mount the named volume {volume!r}"
        )


def test_hub_waits_on_postgres_health_before_migrating() -> None:
    hub = _compose()["services"]["hub"]
    depends_on = hub.get("depends_on")
    assert isinstance(depends_on, dict) and "postgres" in depends_on, (
        "hub must declare a depends_on: postgres condition, not an unconditional dependency"
    )
    assert depends_on["postgres"].get("condition") == "service_healthy", (
        "hub must wait on postgres's healthcheck, not merely on the container starting"
    )


def test_postgres_declares_a_pg_isready_healthcheck() -> None:
    postgres = _compose()["services"]["postgres"]
    healthcheck = postgres.get("healthcheck")
    assert healthcheck, "postgres must declare a healthcheck for hub's depends_on to gate on"
    test = healthcheck.get("test")
    test_str = " ".join(test) if isinstance(test, list) else str(test)
    assert "pg_isready" in test_str


def test_hub_names_a_postgres_db_url() -> None:
    hub = _compose()["services"]["hub"]
    db_url = hub.get("environment", {}).get("BZ_HUB_DB_URL", "")
    assert db_url.startswith("postgresql"), "hub must resolve BZ_HUB_DB_URL to a postgres URL"
    assert "postgres:5432" in db_url, "the DB URL must name the postgres service by its compose service name"


def test_hub_mounts_the_config_file_read_only_for_trusted_proxies() -> None:
    hub = _compose()["services"]["hub"]
    mounts = hub.get("volumes", [])
    config_mounts = [m for m in mounts if isinstance(m, str) and "blizzard-hub.toml" in m]
    assert config_mounts, "hub must mount packaging/docker/blizzard-hub.toml"
    assert config_mounts[0].endswith(":ro"), "the mounted config file must be read-only"


def test_hub_has_no_published_ports_only_reachable_through_the_proxy() -> None:
    hub = _compose()["services"]["hub"]
    assert not hub.get("ports"), "the hub must not publish ports directly — only Caddy fronts it"


def test_caddy_publishes_the_configured_http_and_https_ports() -> None:
    caddy = _compose()["services"]["caddy"]
    ports_str = " ".join(str(p) for p in caddy.get("ports", []))
    assert ":80" in ports_str
    assert ":443" in ports_str


def test_network_declares_an_explicit_subnet() -> None:
    """A deterministic literal, not a docker-assigned range — blizzard-hub.toml's
    trusted_proxies names this subnet, and an undeclared (docker-chosen) range
    would make that trust set nondeterministic across recreations."""
    networks = _compose()["networks"]
    blizzard_net = networks["blizzard"]
    subnets = [c["subnet"] for c in blizzard_net["ipam"]["config"]]
    assert subnets, "the blizzard network must declare an explicit subnet"


def test_trusted_proxies_matches_the_declared_network_subnet() -> None:
    compose_subnets = {c["subnet"] for c in _compose()["networks"]["blizzard"]["ipam"]["config"]}
    hub_config = _hub_config_toml()
    trusted = set(hub_config.get("trusted_proxies", []))
    assert trusted & compose_subnets, (
        f"blizzard-hub.toml's trusted_proxies {trusted} must include the compose network's "
        f"declared subnet(s) {compose_subnets}"
    )


def test_caddyfile_reverse_proxies_to_the_hub_service() -> None:
    text = (_DOCKER_DIR / "Caddyfile").read_text()
    assert "reverse_proxy hub:8421" in text
    assert "BLIZZARD_SITE_ADDRESS" in text


def test_env_example_documents_the_required_variables() -> None:
    text = (_DOCKER_DIR / ".env.example").read_text()
    for var in (
        "BLIZZARD_SITE_ADDRESS",
        "BLIZZARD_HUB_IMAGE",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    ):
        assert var in text, f".env.example must document {var}"

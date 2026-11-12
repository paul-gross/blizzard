"""Serve two disposable runner floors against a local no-auth hub.

Runner identities persist in the hub; the host is not registered. The caller owns
the port and service restoration. The printed ready directory accepts a ``stop`` file.
Run ``uv run python -m tests.support.floor_display_scenario --help`` for arguments.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import socket
import threading
import time
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
import uvicorn

import blizzard.runner.composition as composition
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli.runtime import host_tick_driver
from blizzard.runner.composition import HostProcess, build_host_process
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.host_config import BuiltRunner, ResolvedToken, TokenSource
from blizzard.runner.loop_wiring import PeriodicDriver
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.floor import FLOOR_UNCONFIRMED_GRACE
from tests.support.config_inputs import init_host
from tests.support.host_files import host_config_of
from tests.support.runner_fakes import FakeSubscriptionSampler, usage_limit_mark
from tests.support.runner_join import add_runner


@contextlib.contextmanager
def serve(config: RunnerConfig, host: HostProcess, sock: socket.socket) -> Generator[None]:
    """Own the hosted app and server; stop serving before closing its clients and steppers."""
    hosted = build_hosted_app(config, host)
    server = uvicorn.Server(uvicorn.Config(hosted.app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    started = False
    try:
        thread.start()
        started = True
        with httpx2.Client(base_url=f"http://{config.host}:{config.port}", timeout=1) as client:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and thread.is_alive():
                with contextlib.suppress(httpx2.HTTPError):
                    if client.get("/api/health").status_code == 200:
                        break
                time.sleep(0.1)
            else:
                raise RuntimeError("runner local API did not start")
        yield
    finally:
        if started:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                server.force_exit = True
                thread.join()
        hosted.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch", type=Path, required=True, help="existing scratch parent; a fresh host child is created"
    )
    parser.add_argument("--hub", required=True, help="local hub URL")
    parser.add_argument("--tenant", required=True, help="tenant ID at the local hub")
    parser.add_argument("--port", type=int, required=True, help="free loopback runner port")
    parser.add_argument("--seconds", type=int, default=300, help="maximum serving time")
    args = parser.parse_args()
    hub_url: str = args.hub
    hub = urlsplit(hub_url)
    if hub.scheme != "http" or hub.hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("--hub must be an HTTP loopback URL")
    if not args.scratch.is_dir() or args.seconds <= 0:
        parser.error("--scratch must exist and --seconds must be positive")

    suffix = uuid.uuid4().hex[:8]
    root = args.scratch / f"floor-display-{suffix}"
    root.mkdir(mode=0o700)
    with httpx2.Client(base_url=args.hub, headers={"X-Blizzard-Tenant": args.tenant}) as client:
        identities: list[tuple[str, str]] = []
        for kind in ("bare", "sampled"):
            identity = add_runner(client, f"display-{kind}-{suffix}")
            identities.append(identity)
            print(json.dumps({"enrolled": identity[0], "name": f"display-{kind}-{suffix}"}), flush=True)

    base = init_host(root, hub_url=args.hub, projects=()).runner_config()
    subscription = SubscriptionDeclaration(
        slug="display-plan", name="Display plan", provider=PROVIDER_ANTHROPIC, credentials_path=str(root / "absent")
    )
    base = dataclasses.replace(base, host="127.0.0.1", port=args.port, hub_url=args.hub, projects=())
    configs = tuple(
        dataclasses.replace(
            base,
            name=f"display-{kind}-{suffix}",
            runner_id=identity[0],
            hub_token=identity[1],
            subscriptions=subscriptions,
        )
        for kind, identity, subscriptions in zip(("bare", "sampled"), identities, ((), (subscription,)), strict=True)
    )
    sampler = FakeSubscriptionSampler()
    original = composition.select_sampler

    def select_fake(declaration: SubscriptionDeclaration, **kwargs: object) -> FakeSubscriptionSampler:
        return sampler

    composition.select_sampler = select_fake
    try:
        host = build_host_process(
            dataclasses.replace(host_config_of(configs[0]), subscriptions=(subscription,)),
            [
                BuiltRunner(rid, config, ResolvedToken(token, TokenSource.HOST_FILE))
                for (rid, token), config in zip(identities, configs, strict=True)
            ],
            events=EventBroker(),
        )
    finally:
        composition.select_sampler = original
    try:
        now = datetime.now(UTC).replace(microsecond=0)
        mark = usage_limit_mark(now, resets_in=timedelta(hours=2))
        assert mark.resets_at is not None
        for runner in host.runners:
            runner.stores.pause.record_local_pause(
                paused=True,
                at=now,
                by="display-test",
                report_kind=RUNNER_LOCALLY_PAUSED,
                report_payload=json.dumps({"by": "display-test"}),
            )
        host.runners[0].stores.usage_limits.record_usage_limit_mark(mark)
        host.host_stores.usage_limits.record_usage_limit_mark(subscription.slug, mark)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", args.port))
            with (
                serve(configs[0], host, sock),
                httpx2.Client(base_url=f"http://127.0.0.1:{args.port}") as client,
            ):
                reset = mark.resets_at.isoformat().replace("+00:00", "Z")
                grace = (mark.resets_at + FLOOR_UNCONFIRMED_GRACE).isoformat().replace("+00:00", "Z")

                def check(phase: str) -> dict[str, str | None]:
                    ticks: dict[str, str | None] = {}
                    for index, (rid, _) in enumerate(identities):
                        for endpoint in ("runner", "dashboard"):
                            response = client.get(f"/runners/{rid}/api/{endpoint}")
                            response.raise_for_status()
                            data = response.json()
                            if endpoint == "dashboard":
                                data = data["runner"]
                            hold = data["throttle"]["floor"]["hold"]
                            assert data["pause"]["effective"] and hold["expected_reset"] == reset
                            assert hold["awaits_sample"] is bool(index)
                            assert hold["grace_opens_at"] == (grace if index else None)
                            if phase == "after":
                                assert data["identity"]["state"] == "matched" and data["hub"]["reachable"]
                            ticks[rid] = data["last_tick_at"]
                            print(
                                json.dumps({"phase": phase, "endpoint": endpoint, "id": rid, "hold": hold}), flush=True
                            )
                    return ticks

                check("before")
                drivers = [PeriodicDriver(r.config, interval=lambda: 8, process_graph=r) for r in host.runners]
                host_driver = host_tick_driver(host, tick_seconds=8)
                started: list[PeriodicDriver] = []
                host_started = False
                try:
                    for driver in drivers:
                        driver.start()
                        started.append(driver)
                    host_driver.start()
                    host_started = True
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        attempt = host.host_stores.external_usage.latest_external_usage_attempt(subscription.slug)
                        ready = all(
                            client.get(f"/runners/{rid}/api/runner").json()["last_tick_at"] is not None
                            and runner.identity.current() is not None
                            for (rid, _), runner in zip(identities, host.runners, strict=True)
                        )
                        if ready and attempt is not None and not attempt.ok:
                            break
                        time.sleep(0.1)
                    else:
                        raise TimeoutError("periodic drivers did not register both runners and record a sample miss")
                    ticks = check("after")
                    assert sampler.sample_calls >= 1 and all(ticks.values())
                    info = {"ids": [rid for rid, _ in identities], "reset": reset, "grace": grace, "root": str(root)}
                    (root / "ready.json").write_text(json.dumps(info))
                    print(json.dumps({"ready": info, "ticks": ticks}), flush=True)
                    deadline = time.monotonic() + args.seconds
                    later_reported = False
                    while time.monotonic() < deadline and not (root / "stop").exists():
                        if not later_reported:
                            later = {
                                rid: client.get(f"/runners/{rid}/api/runner").json()["last_tick_at"]
                                for rid, _ in identities
                            }
                            if all(later[rid] and later[rid] != ticks[rid] for rid in ticks):
                                check("after")
                                evidence = {"initial": ticks, "later": later}
                                (root / "later-ticks.json").write_text(json.dumps(evidence))
                                print(json.dumps({"later_ticks": evidence}), flush=True)
                                later_reported = True
                        time.sleep(0.25)
                finally:
                    if host_started:
                        host_driver.stop()
                    for driver in reversed(started):
                        driver.stop()
    finally:
        host.close()


if __name__ == "__main__":
    main()

"""Fleet-trace enablement parsed from OpenTelemetry's own environment variables."""

from __future__ import annotations

import pytest

from blizzard.hub.trace_export.settings import TracingSettings

pytestmark = pytest.mark.unit

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}


def test_no_endpoint_is_disabled() -> None:
    assert TracingSettings.of({}) == TracingSettings("disabled")
    assert TracingSettings.of({"OTEL_EXPORTER_OTLP_ENDPOINT": "  "}).state == "disabled"


@pytest.mark.parametrize("name", ["OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"])
def test_either_endpoint_enables(name: str) -> None:
    settings = TracingSettings.of({name: "http://collector:4318"})
    assert settings.state == "enabled"
    assert settings.enabled()


def test_traces_exporter_none_disables() -> None:
    assert TracingSettings.of({**_ENDPOINT, "OTEL_TRACES_EXPORTER": "none"}).state == "disabled"
    assert TracingSettings.of({**_ENDPOINT, "OTEL_TRACES_EXPORTER": "otlp"}).state == "enabled"


def test_sdk_disabled_true_disables() -> None:
    assert TracingSettings.of({**_ENDPOINT, "OTEL_SDK_DISABLED": "TRUE"}).state == "disabled"
    assert TracingSettings.of({**_ENDPOINT, "OTEL_SDK_DISABLED": "false"}).state == "enabled"


def test_http_protobuf_is_accepted_explicitly() -> None:
    assert TracingSettings.of({**_ENDPOINT, "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"}).enabled()


@pytest.mark.parametrize("name", ["OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"])
def test_another_protocol_is_rejected_naming_its_variable(name: str) -> None:
    settings = TracingSettings.of({**_ENDPOINT, name: "grpc"})
    assert settings == TracingSettings("rejected", setting=name, value="grpc", endpoint="http://collector:4318")
    assert not settings.enabled()
    assert name in settings.rejection_message
    assert "grpc" in settings.rejection_message


def test_the_traces_protocol_overrides_the_general_one() -> None:
    accepted = TracingSettings.of(
        {
            **_ENDPOINT,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        }
    )
    assert accepted.enabled()
    rejected = TracingSettings.of(
        {
            **_ENDPOINT,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json",
        }
    )
    assert rejected == TracingSettings(
        "rejected", setting="OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", value="http/json", endpoint="http://collector:4318"
    )


def test_a_protocol_without_an_endpoint_is_simply_disabled() -> None:
    assert TracingSettings.of({"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}).state == "disabled"


@pytest.mark.parametrize("name", ["OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"])
def test_the_endpoint_keeps_only_its_origin(name: str) -> None:
    raw = "https://user:pw-SECRET1@Collector.example:4318/v1/traces?token=SECRET2#SECRET3"
    settings = TracingSettings.of({name: raw})
    assert settings.endpoint == "https://collector.example:4318"
    assert "SECRET" not in repr(settings)


def test_the_traces_endpoint_wins_over_the_general_one() -> None:
    settings = TracingSettings.of(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://general:1", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://traces:2"}
    )
    assert settings.endpoint == "http://traces:2"


def test_an_endpoint_without_a_port_shows_none() -> None:
    assert TracingSettings.of({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector/x"}).endpoint == "http://collector"


@pytest.mark.parametrize("raw", ["not-a-url-SECRET", "http://host:notaport/p?k=SECRET", "//SECRET@host"])
def test_an_unparseable_endpoint_reads_as_a_placeholder_never_echoed(raw: str) -> None:
    settings = TracingSettings.of({"OTEL_EXPORTER_OTLP_ENDPOINT": raw})
    assert settings.state == "enabled"
    assert settings.endpoint == "<unparseable endpoint>"


def test_a_disabled_setting_holds_no_endpoint() -> None:
    assert TracingSettings.of({}).endpoint is None

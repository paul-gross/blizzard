from __future__ import annotations

import pytest

from blizzard.foundation.trace_attributes import service_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ("service.name=fleet", "fleet"),
        (" service.name = fleet , env=prod", "fleet"),
        ("env=prod,service.name=fleet", "fleet"),
        ("service.name=", "fallback"),
        ("service.name= ", "fallback"),
        ("service.name", "fallback"),
        (" =fleet", "fallback"),
        ("", "fallback"),
    ],
)
def test_service_name_reads_the_resource_attribute_pair(attributes: str, expected: str) -> None:
    assert service_name({"OTEL_RESOURCE_ATTRIBUTES": attributes}, "fallback") == expected


def test_the_service_name_variable_wins_over_the_resource_attribute() -> None:
    environ = {"OTEL_SERVICE_NAME": "explicit", "OTEL_RESOURCE_ATTRIBUTES": "service.name=fleet"}
    assert service_name(environ, "fallback") == "explicit"

"""Exercise census credit against real pytest calls and observed HTTP responses."""

from pathlib import Path

import pytest

from tests.support.aggregate_census import Census


class Reports:
    def __init__(self) -> None:
        self.reports: dict[tuple[str, str], pytest.TestReport] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports[(report.nodeid.rsplit("::", 1)[-1], report.when)] = report


def test_census_rejects_planted_runtime_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_file = tmp_path / "test_planted.py"
    test_file.write_text("""\
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches

class Denied(BaseModel):
    type: str
    title: str
    detail: str
    instance: str

class Other(Denied):
    pass

def scenario(path):
    app = FastAPI()
    @app.get(path, responses={403: {"content": {"application/problem+json": {
        "schema": {"$ref": "#/components/schemas/Denied"}}}}})
    def route():
        return JSONResponse(status_code=403, media_type="application/problem+json",
                            content={"type": "denied", "title": "Denied", "detail": "no", "instance": path})
    response = TestClient(app).get(path)
    spec = app.openapi()
    spec.setdefault("components", {}).setdefault("schemas", {}).update(
        Denied=Denied.model_json_schema(), Other=Other.model_json_schema())
    observation = RefusalObservation("GET", path, response.status_code, response.headers["content-type"],
                                     Denied, response.json())
    return spec, observation

def test_valid():
    spec, observation = scenario("/valid")
    assert coverage_mismatches(spec, {("GET", "/valid")}, [observation]) == []

def test_missing_variant():
    spec, observation = scenario("/missing")
    schema = spec["paths"]["/missing"]["get"]["responses"]["403"]["content"]["application/problem+json"]
    schema["schema"] = {"oneOf": [schema["schema"], {"$ref": "#/components/schemas/Other"}]}
    assert coverage_mismatches(spec, {("GET", "/missing")}, [observation]) == []

def test_wrong_model():
    spec, observation = scenario("/wrong")
    spec["paths"]["/wrong"]["get"]["responses"]["403"]["content"]["application/problem+json"]["schema"] = {
        "$ref": "#/components/schemas/Other"}
    assert coverage_mismatches(spec, {("GET", "/wrong")}, [observation]) == []

def test_wrong_media():
    spec, observation = scenario("/media")
    content = spec["paths"]["/media"]["get"]["responses"]["403"]["content"]
    content["application/json"] = content.pop("application/problem+json")
    assert coverage_mismatches(spec, {("GET", "/media")}, [observation]) == []

def test_fails_after_assertion():
    spec, observation = scenario("/failed")
    assert coverage_mismatches(spec, {("GET", "/failed")}, [observation]) == []
    pytest.fail("later failure")

@pytest.fixture
def broken_teardown():
    yield
    pytest.fail("teardown failure")

def test_fails_in_teardown(broken_teardown):
    spec, observation = scenario("/teardown")
    assert coverage_mismatches(spec, {("GET", "/teardown")}, [observation]) == []
""")
    census = Census()
    reports = Reports()
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main([str(test_file), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"], plugins=[census, reports])
    assert result == pytest.ExitCode.TESTS_FAILED
    assert reports.reports[("test_valid", "call")].passed
    for name, message in (
        ("test_missing_variant", "unobserved: GET /missing 403 application/problem+json Other"),
        ("test_wrong_model", "missing model: Denied"),
        ("test_wrong_media", "missing media type: application/problem+json"),
        ("test_fails_after_assertion", "later failure"),
    ):
        report = reports.reports[(name, "call")]
        assert report.failed and message in str(report.longrepr)
    assert reports.reports[("test_fails_in_teardown", "call")].passed
    assert reports.reports[("test_fails_in_teardown", "teardown")].failed
    assert census.covered == {("GET", "/valid")}
    assert {(item.method, item.path) for item in census.observations} == {("GET", "/valid")}

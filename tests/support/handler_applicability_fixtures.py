"""Fixtures for transitive handler dispatch analysis."""


def imported_helper() -> None:
    raise ValueError()


def local_import_caller() -> None:
    import tests.support.handler_applicability_fixtures as fixture

    fixture.imported_helper()

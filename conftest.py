"""Suite-wide pytest wiring for every collected directory: ``tests/``, the exemplar's tests, and the store-migration
tests colocated beside their revisions under ``src/``."""

# The fakes import the runner's CLI, which reads packaged files at import; the suite plugin's repo-read guard installs
# after them, as it did when both lived in ``tests/conftest.py``.
pytest_plugins = ("tests.runner_init_fakes", "tests.suite_plugin")

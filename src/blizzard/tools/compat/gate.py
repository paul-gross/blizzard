"""The pass/fail decision over classifier findings."""

from __future__ import annotations

import re

#: A Conventional Commits breaking marker on a commit subject: ``type!:`` or ``type(scope)!:``.
BREAKING_COMMIT_MARKER = re.compile(r"^\w+(\([^)]*\))?!:")

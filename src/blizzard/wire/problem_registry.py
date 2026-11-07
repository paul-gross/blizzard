"""Every registered problem type: the base registry in ``src/blizzard/wire/problem.py``, then each family module."""

from blizzard.wire.problem import PROBLEM_TYPES
from blizzard.wire.problem_types_admin import ADMIN_PROBLEM_TYPES
from blizzard.wire.problem_types_chunks import CHUNKS_PROBLEM_TYPES
from blizzard.wire.problem_types_garden import GARDEN_PROBLEM_TYPES
from blizzard.wire.problem_types_runners import RUNNERS_PROBLEM_TYPES

ALL_PROBLEM_TYPES: frozenset[str] = (
    PROBLEM_TYPES | ADMIN_PROBLEM_TYPES | CHUNKS_PROBLEM_TYPES | GARDEN_PROBLEM_TYPES | RUNNERS_PROBLEM_TYPES
)

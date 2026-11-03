"""A store-and-forward lane's applied-seq high-water mark, advanced by compare-and-set."""


class HighWaterConflict(Exception):
    """A mark advance was decided on a seq another push has since moved the mark past: it advanced nothing."""

    def __init__(self, owner_id: str, *, expected: int) -> None:
        super().__init__(f"the high-water mark of {owner_id} moved past {expected} — re-read it")
        self.owner_id = owner_id
        self.expected = expected

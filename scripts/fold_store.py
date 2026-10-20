"""Fold both stores' migration history up to a floor release into one baseline revision each.

    uv run python scripts/fold_store.py <floor-tag>

``<floor-tag>`` is the ``oldest`` entry of ``blizzard-compat releases --json``. Needs ``gh`` access to download the
release's wheel. Rewrites the floor revision of each tree as a baseline, deletes the revisions beneath it and their
colocated tests, and touches nothing above the floor; a tree already folded to that floor is left as it is."""

from __future__ import annotations

import argparse
from pathlib import Path

from blizzard.tools.store_fold.fold import fold_store
from blizzard.tools.store_fold.tree import FoldError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("floor_tag", help="the release tag the baseline stands for, such as v0.2.0")
    args = parser.parse_args()
    try:
        folds = fold_store(Path(__file__).resolve().parent.parent, args.floor_tag)
    except FoldError as refusal:
        print(f"refused: {refusal}")
        return 1
    for fold in folds:
        if fold.baseline is None:
            print(f"{fold.tree}: already folded to {args.floor_tag}, nothing to do")
        else:
            print(f"{fold.tree}: baseline {fold.baseline.name}, {len(fold.deleted)} files deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Build — judgement

Judge the work as it now stands rather than only this turn's output — an increment an earlier attempt completed still
counts. The criteria are:

- the change implements the work item's intent;
- the work is committed, pushed, and declared with `blizzard runner artifact commit` for every repo touched.

Before recording the verdict, run `blizzard runner artifact commit` on this attempt for every repo the chunk touches. A
declaration does not carry over between attempts: coverage is measured per attempt and each re-attempt runs under a
fresh lease, so a tip an earlier attempt declared leaves this one uncovered.

Record `pass` only when every criterion holds; the work then hands to the `review` node. Record `fail` otherwise, which
re-enters the `build` node with the failing attempt's output.

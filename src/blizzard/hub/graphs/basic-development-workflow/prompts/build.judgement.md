# Build — judgement

You are closing a build node-step. Judge the work as it now stands rather than only what this turn produced: an
increment an earlier attempt finished still counts.

Coverage is checked per attempt under a fresh lease, so a declaration does not carry over — an earlier attempt's
declared tip does not satisfy this one. Where this attempt has not yet declared every repo the chunk touches, declare
them before you record a verdict.

| Outcome | Record it when                                                                                                                                  |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `pass`  | The change implements the work item's intent, and every touched repo is committed, pushed, and declared with `blizzard runner artifact commit`. |
| `fail`  | Any of those conditions does not hold.                                                                                                          |

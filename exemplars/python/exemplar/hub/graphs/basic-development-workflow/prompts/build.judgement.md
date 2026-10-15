# Build — judgement

You are closing a build node-step. Judge the work as it now stands, including increments an earlier attempt finished. A
declaration does not carry over between attempts: declare every repo the chunk touches before you record a verdict.

| Outcome | Record it when                                                                                                                                  |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `pass`  | The change implements the work item's intent, and every touched repo is committed, pushed, and declared with `blizzard runner artifact commit`. |
| `fail`  | Any of those conditions does not hold.                                                                                                          |

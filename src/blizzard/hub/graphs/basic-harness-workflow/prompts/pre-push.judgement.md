# Pre-push — judgement

You are closing a pre-push node-step: record the integration verdict.

Before you record the verdict, submit the `pre-push-summary` asset — this verdict's assessment payload — with
`blizzard runner artifact create --name pre-push-summary`, content on stdin.

Triage on the state as it now stands, counting work an earlier attempt at this node did.

| Outcome         | Record it when                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| `clean`         | The rebase applied with no conflicts, or only trivial mechanical ones, and the read-back holds.                     |
| `insignificant` | Conflicts were resolved without semantic choices, and the read-back holds.                                         |
| `significant`   | A resolution made a semantic choice, the rebase materially reshaped the change, or the read-back surfaced failures. |

When torn between two severities, take the more cautious route: `significant` over `insignificant`, `insignificant` over
`clean`.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the closing `retrospective` node should know.
Unlike `bas-dwf`, this lane's `prepush` lineage never saw `build` or `iterate`, so this is the only session-local record
of the integration leg the closing node can draw on.

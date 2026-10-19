# Pre-push — judgement

You are closing a pre-push node-step: record the integration verdict.

Before you record the verdict, submit the `pre-push-summary` asset — this verdict's assessment payload — with
`blizzard runner artifact create --name pre-push-summary`, content on stdin.

Triage on the state as it now stands, counting work an earlier attempt at this visit did — a rebase an interrupted
attempt already pushed still needs its verdict. A resolution an earlier visit to this node already ruled on is not ruled
again: the work came back here through the route that verdict picked, and that route re-earned it. If the branches still
carry the tips this node last declared and the base has not moved past them, there is nothing new to triage — rule on
the checks alone.

| Outcome         | Record it when                                                                                                            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `clean`         | The rebase applied with no conflicts, or only trivial mechanical ones, and lint and the targeted unit tests are green.    |
| `insignificant` | Conflicts were resolved without semantic choices, and the targeted checks are green.                                      |
| `significant`   | A resolution made a semantic choice, the rebase materially reshaped the change, or the targeted checks surfaced failures. |

When torn between two severities, take the more cautious route: `significant` over `insignificant`, `insignificant` over
`clean`.

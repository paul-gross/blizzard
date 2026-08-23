# Pre-push — judgement

You are closing a pre-push node-step: record the integration verdict.

Before you record the verdict, submit the `pre-push-summary` asset — this verdict's assessment payload — with
`blizzard runner artifact create --name pre-push-summary`, content on stdin.

Triage on the state as it now stands, counting work an earlier attempt at this node did.

| Outcome         | Record it when                                                                                                            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `clean`         | The rebase applied with no conflicts, or only trivial mechanical ones, and lint and the targeted unit tests are green.    |
| `insignificant` | Conflicts were resolved without semantic choices, and the targeted checks are green.                                      |
| `significant`   | A resolution made a semantic choice, the rebase materially reshaped the change, or the targeted checks surfaced failures. |

When torn between two severities, take the more cautious route: `significant` over `insignificant`, `insignificant` over
`clean`.

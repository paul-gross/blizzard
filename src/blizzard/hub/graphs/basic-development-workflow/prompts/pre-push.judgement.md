# Pre-push rebase — judgement

Render the integration verdict. Your assessment payload is the `pre-push-summary` asset — if you have not yet run `blizzard runner artifact create --name pre-push-summary` with it on stdin, do that now, before you record this verdict.

- `clean` — the rebase applied with no conflicts, or only trivial mechanical ones, and lint plus the targeted unit tests are green. The chunk proceeds to delivery.
- `insignificant` — conflicts were resolved without semantic choices and the targeted checks are green. The rebased result rides back into review for cold eyes.
- `significant` — a resolution required a semantic choice, the rebase materially reshaped the change, or the targeted checks surfaced failures. This lane has no verify node to re-earn, so the work rides back into build instead.

Triage on the state as it now stands, including work an earlier attempt at this node did. When torn between two severities, choose the more cautious route: significant over insignificant, insignificant over clean.

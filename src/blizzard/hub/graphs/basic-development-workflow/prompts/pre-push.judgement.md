# Pre-push rebase — judgement

Render the integration verdict. Your assessment payload is the `pre-push-summary` asset — if you have not yet run `blizzard runner attach --name pre-push-summary` with it on stdin, do that now before you record this verdict.

Select `clean` if the rebase applied with no conflicts (or only trivial mechanical ones) and lint plus the targeted unit tests are green — the chunk proceeds to delivery. Select `insignificant` if conflicts were resolved without semantic choices and the targeted checks are green — the rebased result rides back into review for cold eyes. Select `significant` if any resolution required a semantic choice, the rebase materially reshaped the change, or the targeted checks surfaced failures — this lane has no verify node to re-earn, so the work rides back into build instead.

When torn between two severities, choose the more cautious route (significant over insignificant, insignificant over clean).

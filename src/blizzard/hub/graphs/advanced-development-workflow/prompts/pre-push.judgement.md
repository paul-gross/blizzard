# Pre-push rebase — judgement

Render the integration verdict. The `pre-push-summary` asset must be published before it.

- `clean` — the rebase applied with no conflicts, or only trivial mechanical ones, and lint plus the targeted unit tests
  are green. The chunk proceeds to delivery.
- `insignificant` — conflicts were resolved without semantic choices and the targeted checks are green. The rebased
  result rides back into review for cold eyes.
- `significant` — a resolution required a semantic choice, the rebase materially reshaped the change, or the targeted
  checks surfaced failures. The work rides back into verify to re-earn its verification.

Triage on the state as it now stands, including work an earlier attempt at this node did. When torn between two
severities, choose the more cautious route: significant over insignificant, insignificant over clean.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.

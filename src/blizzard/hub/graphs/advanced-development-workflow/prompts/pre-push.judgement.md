# Pre-push rebase — judgement

Render the integration verdict. The `pre-push-summary` asset must be published before it.

- `clean` — the rebase applied with no conflicts, or only trivial mechanical ones, and lint plus the targeted unit tests
  are green. The chunk proceeds to delivery.
- `insignificant` — conflicts were resolved without semantic choices and the targeted checks are green. The rebased
  result rides back into review for cold eyes.
- `significant` — a resolution required a semantic choice, the rebase materially reshaped the change, or the targeted
  checks surfaced failures. The work rides back into verify to re-earn its verification.

Triage on the state as it now stands, including work an earlier attempt at this visit did — a rebase an interrupted
attempt already pushed still needs its verdict. A resolution an earlier visit to this node already ruled on is not ruled
again: the work came back here through the route that verdict picked, and that route re-earned it. If the branches still
carry the tips this node last declared and the base has not moved past them, there is nothing new to triage — rule on
the checks alone. When torn between two severities, choose the more cautious route: significant over insignificant,
insignificant over clean.

Alongside your verdict, submit this node's retrospective: run `blizzard runner artifact create --name retrospective`
with a few honest lines on stdin — what went well, what didn't, and what the next node or run should know.

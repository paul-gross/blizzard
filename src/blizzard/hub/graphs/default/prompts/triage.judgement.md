# Triage — judgement

Select the route your triage rationale justifies. The rationale rides forward as the `triage-findings` asset — if you
have not yet run `blizzard runner artifact create --name triage-findings` with it on stdin, do that now, before
recording this verdict.

- `already-done` — only on positive evidence that every work item's intent is already satisfied by the environment's
  current code. The chunk closes without entering a lane.
- `harness` — the work's own subject is the agentic harness itself: an agent skill, a convention rule, a graph prompt,
  or agent-facing docs. Applies regardless of the item's complexity label. The chunk migrates to the frontier-tier
  `bas-hwf` lane and starts at its build node.
- `basic` — small, well-specified work that is not harness work. The chunk migrates to the lightweight `bas-dwf` lane
  and starts at its build node.
- `advanced` — work that warrants a plan. The chunk migrates to the plan-gated `adv-dwf` lane and starts at its plan
  node.

A migration is one-way: the chunk leaves this graph and does not come back. Route on the evidence you recorded, not on a
hunch.

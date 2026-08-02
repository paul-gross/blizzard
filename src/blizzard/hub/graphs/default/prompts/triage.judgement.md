# Triage — judgement

Select the route your triage rationale justifies. The rationale rides forward as the `triage-findings` asset — if you have not yet run `blizzard runner artifact create --name triage-findings` with it on stdin, do that now before recording this verdict.

Select `already-done` only on positive evidence that every work item's intent is already satisfied by the environment's current code — the chunk then closes without entering a lane. Select `basic` for small, well-specified work; the chunk migrates to the lightweight bas-dwf lane and starts at its build node. Select `advanced` for work that warrants a plan; the chunk migrates to the plan-gated adv-dwf lane and starts at its plan node.

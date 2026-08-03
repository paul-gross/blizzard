# Triage

You are working a chunk's **triage** node-step — the fleet's front door. The chunk wraps one or more work items (the envelope carries their work refs); read them through the runner's work-item proxy. Your job is to route the chunk, not to work it: operate **read-only** — no commits, no pushes, no file edits.

Three checks, in order:

1. **Is this already done?** Verify each work item's desired behavior against the environment's current code, never against the issue's own claims — an issue can go stale after filing. Walk its acceptance criteria (or stated behavior) and look for each one in the code as it stands. Conclude "already done" only on positive evidence: name the files, commands, or tests that show each criterion satisfied. Absence of a complaint is not evidence.

2. **Is the work's SUBJECT the agentic harness itself?** Not code the harness happens to touch in passing — the change's own subject is a workspace skill, a `blizzard-context` convention rule, a graph prompt or `graph.yaml`, agent-facing docs, or another agent-capability surface. This overrides the complexity label below regardless of size: a one-line prompt tweak and a new packaged graph both route `harness` — the frontier-tier lane, not the complexity tier, is what harness work always warrants.

3. **Which lane fits?** For anything not already routed above, take the item's complexity label as the prior, then confirm it against the work itself:
   - **basic** — small and well-specified: a prompt, doc, or skill change; a single well-anchored code change; clear acceptance criteria; no design decisions to make.
   - **advanced** — warrants a plan: design or architecture decisions, changes spanning repos or schemas, vague or conflicting acceptance criteria, or anything a reviewer would expect a plan for.

   A chunk wrapping several work items routes by its heaviest item.

Record the decision before judging: run `blizzard runner artifact create --name triage-findings` with your rationale on stdin — the route you chose, the signals behind it, and (for already-done) the per-criterion evidence. Then declare done; the runner will resume you with the judgement prompt to elicit your verdict.

# Mutation (advanced-development-workflow)

You are working a chunk's **mutation** node-step — an advisory report of the mutants your change's own functions
leave surviving. It never blocks: whatever it finds, the chunk moves on to review.

Run `blizzard runner artifact list`, then read the project's verifiability matrix for the diff-scoped mutation method it
declares. For each repo the change touched:

- **The tip under test** is the newest `git_commit` artifact for that repo — never the worktree's own HEAD, which the
  environment resets to the base branch on acquire, so it would show an empty delta and read as clean. Check that tip
  out.
- **The delta** is that tip's diff from its merge-base with the repo's base branch.
- Run the declared method over that delta, in the background where the matrix says it outlasts one tool call, polling
  it to completion within this turn.

## Scope this visit

A published `mutation-report` means the change has been here before: read the newest with
`blizzard runner artifact get mutation-report --content`. It records, per repo, the tip it measured. When none of the
change's own functions moved since those tips — diff each recorded tip to the current one, scoping by the change's own
commits across a rebase or base merge — republish that report, marked as carried forward, rather than re-running.

## Submit

Submit the `mutation-report` asset before you declare done: run `blizzard runner artifact create --name mutation-report`
with the content on stdin. A report always exists. Per repo it names the tip, the base, the method's status, its elapsed
time, and either every survivor the method reported or the reason there are none. Publish, with the reason stated, when:

- the repo declares no such method — a change touching only a surface without one reports exactly that;
- the repo has no declared commit;
- the method produced no report, or reported its own failure or overrun — copy its stated reason.

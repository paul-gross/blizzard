You are re-entering this node after pre-push triaged the integration `significant`: a rebase that needed semantic
conflict choices or materially reshaped the change, or a failure its read-back surfaced. The `pre-push-summary` asset in
this envelope records each conflict and its chosen resolution, and names any failing check.

After a rebase, the branches were rewritten, so any prior view of what each repo carries is stale. Revalidate the
rebased result here — read it back, concentrating on whatever the conflict resolutions touched and on anything else the
rebase disturbed.

When the summary names a flaky test — a CI check that failed intermittently on this chunk's PR — the flake is a defect
in the suite: fix it at its root (the race, the shared state, the timing assumption) as its own commit, separate from
the change, and never loosen, retry-wrap, or skip the test.

# Pre-push — re-entry after a deliver bounce

Deliver bounced this chunk back instead of landing it, so you are arriving at this node again. It reported one of two
causes. `conflict` means a repo's PR read dirty — a real merge conflict against the current base. `failure` means either
the `land` action itself failed, or a repo's own CI check failed on the PR — read
`blizzard runner artifact get delivery-findings --node deliver --content` to tell the two apart; it names which repo and
check, when one failed.

Redo this node's whole job, for every repo still ahead, against the base as it now stands. Assess the repos as they
stand now rather than as the bounce described them. A `failure` from a self-clearing state — a pass that raised, a
mergeability state still computing, a momentary forge hiccup — may already have cleared on its own; it has only once
every repo's PR reads mergeable and no check on the head sha is red. A red check conclusion never clears on its own. A
`failure` naming a real CI check on this chunk's own change is not a rebase problem — do not attempt to fix it here;
triage it `significant` so the chunk re-enters build with the finding, rather than pushing an unfixed defect back to
deliver. A check that looks flaky is triaged `significant` the same way, after investigation rather than blindly: read
the failing job's log, check whether the same test fails elsewhere (the base branch's recent runs, other PRs), wait for
the workflow run to conclude (`gh run watch <run-id>` — the forge refuses a re-run while it is in progress), then
`gh run rerun <run-id> --failed`. A re-run that passes shows a flaky test or harness, itself a defect in the suite; one
that fails the same way shows the change's own. Either way, name the test, the failure, and its likely cause in the
`pre-push-summary`. A `failure` with a `delivery-findings/foreign-head` artifact names a foreign commit on the PR head
and is triaged `significant` the same way, never waved through. A multi-repo chunk lands one repo at a time, so some
repos may already be landed — a repo whose PR is already merged needs no rework.

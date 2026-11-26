# Pre-release — re-entry after a deliver bounce

Deliver bounced this chunk back instead of landing it, so you are arriving at this node again. It reported one of two
causes. `conflict` means a repo's PR read dirty — a real merge conflict against the current base. `failure` means either
the `land` action itself failed, or a repo's own CI check failed on the PR — read
`blizzard runner artifact get delivery-findings --node deliver --content` to tell the two apart; it names which repo and
check, when one failed.

Redo this node's whole job, for every repo still ahead, against the base as it now stands. Assess the repos as they
stand now rather than as the bounce described them. A `failure` from a self-clearing state — a pass that raised, a
mergeability state still computing, a momentary forge hiccup — may already have cleared on its own; it has only once
every repo's PR still open reads mergeable and no check on the head sha is red. A red check conclusion never clears on
its own. A `failure` naming a real CI check on this chunk's own change is not a rebase problem — do not attempt to fix
it here; triage it `significant`, naming the check and its failure in the `pre-release-summary`, so the chunk re-enters
iterate with the finding, rather than pushing an unfixed defect back to deliver. A check that looks flaky is triaged
`significant` the same way, after investigation rather than blindly: read the failing job's or check's logs and compare
the failure with recent base-branch runs and other changes. Through the configured forge tools, wait for the relevant
run or pipeline to complete, then retry the failed jobs or checks once, where the forge supports it and you are
authorized. A retry that passes shows a flaky test or harness, itself a defect in the suite; one that fails the same way
shows the change's own. If logs, waiting or retry are unsupported or inaccessible, report that gap and the investigation
actually done; never claim a retry, a completed run or a diagnosis that did not happen, and never work around
authorization. Either way, name the test or check, the failure, and its likely cause in the `pre-release-summary`. A
`failure` with a `delivery-findings/foreign-head` artifact names a foreign commit on the PR head and is triaged
`significant` the same way, never waved through. A multi-repo chunk lands one repo at a time, so some repos may already
be landed — a repo whose PR is already merged needs no rework.

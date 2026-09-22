# Pre-push — re-entry after a deliver bounce

Deliver bounced this chunk back instead of landing it, so you are arriving at this node again. It reported one of two
causes. `conflict` means a repo's PR read dirty — a real merge conflict against the current base. `failure` means
either the land script itself broke or crashed, or a repo's own CI check failed on the PR — read
`blizzard runner artifact get delivery-findings --node deliver --content` to tell the two apart; it names which repo
and check, when one failed.

Redo this node's whole job, for every repo still ahead, against the base as it now stands. Assess the repos as they
stand now rather than as the bounce described them: a mechanical `failure` (a script crash, a transient forge hiccup)
may already have cleared on its own. A `failure` naming a real CI check on this chunk's own change is not a rebase
problem — do not attempt to fix it here; triage it `significant` so the chunk re-enters iterate with the finding,
rather than pushing an unfixed defect back to deliver. A multi-repo chunk lands one repo at a time, so some repos may
already be landed — a repo whose PR is already merged needs no rework.

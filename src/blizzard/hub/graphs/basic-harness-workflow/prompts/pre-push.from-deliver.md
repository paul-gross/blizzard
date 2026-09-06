# Pre-push — re-entry after a deliver bounce

Deliver bounced this chunk back instead of landing it, so you are arriving at this node again. It reported one of two
causes. `conflict` means a repo's base moved after this chunk rebased, so the forge refused the no-longer-fast-forward
update. `failure` means the land script itself broke or crashed instead of reporting an outcome. Neither bounce is a
judgement on the change, which already cleared build and review.

Redo this node's whole job, for every repo still ahead, against the base as it now stands. Assess the repos as they
stand now rather than as the bounce described them, and on a `failure` bounce extend that assessment to the branch and
land-script state. A multi-repo chunk lands one repo at a time, so some repos may already be landed — a repo whose base
already carries this chunk's commit needs no rework.

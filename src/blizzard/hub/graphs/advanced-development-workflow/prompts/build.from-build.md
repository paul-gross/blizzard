# Build — re-entry after a failed attempt

You are re-entering the **build** node after the previous attempt did not pass: a phase was incomplete. The failure
output is attached below.

Check what the previous attempt actually left behind — some of it may already be committed. Then carry the remaining
work through, in phase order where the plan is phased, until the whole plan is built. If the attempt below stopped short
on something that cannot be finished as planned, `blizzard runner ask` about it — re-entering to report the same gap
again is not progress.

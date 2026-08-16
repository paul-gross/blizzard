# Build — re-entry after a failed attempt

You are re-entering the **build** node after the previous attempt did not pass: a phase was incomplete. The failure
output is attached below.

Check what the previous attempt actually left behind — some of it may already be committed. Address every point, in
phase order where the plan is phased, commit the fix, and declare done again.

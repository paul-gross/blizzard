# Build — re-entry after resolve found a defect

You are re-entering the **build** node because delivery stalled and the resolve node diagnosed a defect: CI on the PR is
red because the change itself fails against the current base, or because a test or harness in the suite is flaky — not a
merge conflict, not a self-clearing forge state. The `resolve-report` asset in your envelope names the failing checks
and what resolve observed.

Your commits are intact on the declared branch (`blizzard runner artifact get branch --node plan --content`); nothing
has landed. Confirm what the branch carries before you act — resolve may have merged the base into it. Address each
finding, commit, push, and re-declare the tip. A report naming a flaky test is a defect in the suite, not a nuisance:
fix the flake at its root — the race, the shared state, the timing assumption — as its own commit, and never loosen,
retry-wrap, or skip the test. The chunk re-earns its verification on the way back to delivery.

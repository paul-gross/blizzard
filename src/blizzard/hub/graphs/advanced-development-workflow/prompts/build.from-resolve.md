# Build — re-entry after resolve found a real defect

You are re-entering the **build** node because delivery stalled and the resolve node diagnosed a real defect: CI on the PR is red because the change itself fails against the current base. Not a merge conflict, not a transient forge state. The `resolve-report` asset in your envelope names the failing checks and what resolve observed.

Your commits are intact on the `feat/<slug>` feature branch; nothing has landed. Confirm that before you act — check what the branch carries now, since resolve may have merged the base into it.

Address each finding, commit, push, and re-declare the tip. The chunk re-earns its verification on the way back to delivery.

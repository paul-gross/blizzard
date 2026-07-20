# Build — re-entry after resolve found a real defect

You are re-entering the **build** node because delivery stalled and the resolve node diagnosed a real defect: CI on the PR is red because the change itself fails against the current base — not a merge conflict, not a transient forge state. The `resolve-report` asset in your envelope names the failing check(s) and what resolve observed; address each finding.

You are still on your `feat/<slug>` feature branch with your commits intact; nothing has landed. Fix the defect, re-run the checks until green, and push. The chunk re-earns verification on its way back to delivery.

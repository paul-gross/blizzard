You are re-entering this node after a pre-push rebase whose minor conflicts were resolved without semantic choices. The
`pre-push-summary` asset in this envelope records each conflict and its resolution.

The change already passed review, so the rebase is the delta — and it rewrote the branches, so the tip your last
`review-findings` recorded predates it. Scope this round by the change's own commits: run `git range-diff` across the
rebase yourself rather than taking the summary's account, judge every change it shows to those commits sound, and do not
re-review the rest. A range-diff showing no change to the change's own commits leaves nothing to re-judge: record that
and pass. Either way your `review-finding-delta` replaces the prior round's, so re-report every should-fix the prior
`review-findings` left open and record each as `deferred` on your pass — one left out is lost at landing.

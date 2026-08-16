# Build — re-entry after failed verification

You are re-entering the **build** node after the verify node returned `fail`. The `verification-report` asset in this
envelope records what was exercised and what failed.

Your commits are intact on the feature branch; nothing has landed. Verify ran in its own session, so any fix or new
verification method it committed is on the branch without being in your conversation — that work is accounted for, not
the foreign work `build`'s stop-and-ask rule is about. Address every failure, commit the fix, push, and re-declare the
tip before you declare done again. The work returns to verification.

There is no refutation channel here, unlike a review bounce: a failed verification method is a mechanical fact, not a
judgement to argue with — either the change is wrong, or the method is. Fix whichever it is.

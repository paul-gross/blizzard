# Build — re-entry after a failed review

You are re-entering the **build** node after the review node found blocking issues. The `review-findings` asset in this envelope records each one.

Your commits are intact on the feature branch; nothing has landed. Check each finding against the code as it now stands before fixing it — a finding an earlier attempt already resolved needs no second fix. Answer every finding — by fixing it, or by refuting it — then commit, push, re-declare the tip, and declare done again.

## Fixing versus refuting

A finding you disagree with is not a finding to quietly ignore. Refute it, on the record.

Refute a finding when it is factually wrong, rests on a false premise, or demands work this change's scale does not warrant. Do not refute one merely because fixing it is inconvenient, or because you would have made a different call — a defensible finding you simply dislike gets fixed.

This channel exists because `review` is a **full cold read every pass**, not a delta. Without a refutation on the record, a finding you deliberately declined is re-discovered and re-raised every round.

Record each refutation in the `review-finding-refutes` asset: the finding's **anchor** (`<repo>/<path>:<line>`) copied verbatim, the id you are answering, and the argument with its evidence. The anchor is what the reviewer matches on — a fresh cold pass renumbers its findings, so an id alone cannot survive it.

Refuting is a claim to be adjudicated, not a veto. The reviewer will either accept a refutation and not raise it again, or reject it and answer your argument.

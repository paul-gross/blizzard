# Re-entering build from review

You are re-entering build after review found blocking issues, each recorded in the envelope's `review-findings` asset.
Your commits are intact on the feature branch; nothing has landed. Answer every finding by fixing or refuting it, then
commit, push, re-declare the tip, and declare done again.

Check each finding against the current code first — one an earlier attempt already resolved needs no second fix.

Refute a finding that is factually wrong, rests on a false premise, or demands work beyond the change's scale — never
merely because fixing is inconvenient; a defensible finding you dislike gets fixed. Record each refutation in
`review-finding-refutes`: the finding's anchor (`<repo>/<path>:<line>`) copied verbatim, the id answered, and the
argument with evidence — the reviewer matches on the anchor, since a fresh pass renumbers ids. A refutation is a claim
to be adjudicated, not a veto: the reviewer either accepts it and stops raising the finding, or rejects it and answers
your argument. Review is a full cold read every pass, so a declined finding with no refutation on record is re-raised
every round.

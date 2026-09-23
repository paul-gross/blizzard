# Survey — judgement

Report what the sweep produced.

Choose `found` if the `survey` asset holds at least one candidate.

Choose `undeclared` if the target's gardening-axes registry declares no entry for your routine's axis, you already
asked (or found a prior, already-answered ask on `blizzard runner chunk history` naming this same axis) and the
registry still does not declare it, and both `survey` and `delta` are published empty. Do not choose this before
asking: an axis simply not yet checked is not the same claim as one asked about and still missing. Do not choose it
with candidates attached — an undeclared axis and a sweep taken by some other yardstick are different claims.

Choose `empty` if you swept the whole scope you were given and found nothing wanting. This is a success, not a
failure, and it still delivers: the run's measurement is the product of a clean pass. Do not choose it before the
run's empty `delta` asset is submitted — delivery reads that artifact on this path, and no later node runs to write
it.

Do not choose `empty` because the sweep was hard, because the scope was unclear, or because you ran short of context.
If you could not actually cover the scope, say so and let the retry or the escalation handle it — a run that reports
nothing missing without having looked writes a false datapoint into a series somebody will trust.

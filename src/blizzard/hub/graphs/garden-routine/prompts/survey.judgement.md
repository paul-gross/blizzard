# Survey — judgement

Report what the sweep produced.

Choose `found` if the `survey` asset holds at least one candidate.

Choose `excessive` if you stopped before enumerating because the scope is past what one pass can inventory, and the
survey holds exactly one `excessive-scope` candidate. Do not choose it with an inventory attached: a bail-out and a
partial list are different claims, and a submission making both is unreadable as either.

Choose `clean` if you swept the scope you were given and observed nothing worth recording. This is a success, not a
failure, and it still delivers: the run's measurement and the revision it read are the product of a clean pass, and the
flat trendline they draw is the evidence that the garden is being held. Do not choose it before the run's empty `delta`
asset is submitted — delivery reads that artifact on this path, and no later node runs to write it.

Do not choose `clean` because the sweep was hard, because the scope was unclear, or because you ran short of context. If
you could not actually cover the scope, say so and let the retry or the escalation handle it — a run that reports clean
without having looked writes a false datapoint into a series somebody will trust.

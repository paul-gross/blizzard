# Reconcile — judgement

Choose `converged` when the delta is complete: every survey candidate resolved as an addition or as a transformation of
a live finding, and every live finding this run actually visited accounted for.

Choose `nothing-to-propose` only when the delta holds no `add` — nothing new arrived — and every finding still standing
(every `observed`, and every finding your bucket held that the delta left untouched) is named in at least one open
proposal's own `findings` list, fetched with `blizzard runner garden proposals`. A live finding not cited by any open
proposal has no response yet, however old it is — that is `converged`, so `propose` gets the chance to draft one.

If you could not fetch the routine's live findings at all, choose neither: say so, and let the retry handle it — a
delta assembled without knowing what the routine already holds is a duplicate storm with a timestamp on it.

If the proposals read failed outright, or came back empty in a way you cannot trust as a genuine "nothing is open"
answer, do not let that produce `nothing-to-propose`. Choose `converged` instead — an untrustworthy proposals read
degrades to the branch that still gets a person another look, never to the one that stops here.

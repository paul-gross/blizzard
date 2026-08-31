# Reconcile — judgement

Choose `converged` when the delta is complete: every survey candidate resolved as an addition or as a transformation of
a live finding, and every live finding this run actually visited accounted for.

Choose `nothing-to-propose` when the delta holds only transformations — nothing new arrived, and nothing still standing
lacks a response somebody has already seen. The delta still delivers; there is simply nothing to draft.

If the delta is incomplete because you could not fetch the routine's live findings, do not choose either. Say so, and
let the retry handle it: a delta assembled without knowing what the routine already holds is a duplicate storm with a
timestamp on it.

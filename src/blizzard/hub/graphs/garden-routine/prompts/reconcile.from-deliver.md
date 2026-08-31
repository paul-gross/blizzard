# Reconcile — after a rejected delivery

Delivery rejected your artifact on shape validation and wrote nothing. The failure is attached — read it with
`blizzard runner artifact get garden-delivery-failure --content`.

This is a structural problem, not a judgment one: a malformed entry, a missing required field, a `fin_` id that is not
live on this routine, or a commit reference that does not resolve. Your findings are not in question — the shape they
were submitted in is.

Re-read the `findings` doc, fix what the failure names, and resubmit the delta. Change nothing about what you concluded
while you are in there; correcting a format error is not an invitation to revisit the matching.

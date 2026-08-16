# Build — judgement

Assess the build you just completed. The `reviewed-plan` — the plan of record as it left the gate — is fully
implemented: where it is phased, every phase in order and scoped as planned. Your work is committed, pushed, and
declared with `blizzard runner artifact commit` for each repo you touched **on this attempt**.

Judge the **work** as it now stands, not only what you did this turn: an increment an earlier attempt completed still
counts, and you do not redo it.

The **declaration** is different, and does not carry over. Coverage is checked per attempt, and a re-attempt runs under
a fresh lease — so a tip declared by an earlier attempt does not satisfy this one. If you did not run
`blizzard runner artifact commit` on this attempt for every repo the chunk touches, do that before recording your
verdict; re-declaring an unchanged tip is harmless, omitting it is not.

Select `pass` only if all of that holds — the work then hands to the verify node. Select `fail` if a phase is
incomplete, or the work is not committed, pushed, and declared. The failure output is attached when the build node is
re-entered.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run
`blizzard runner artifact create --name retrospective` with a few honest lines on stdin — what went well, what didn't,
and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.

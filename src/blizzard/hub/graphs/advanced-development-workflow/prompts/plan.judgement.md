# Plan — judgement

Assess the plan you just authored. Every planned change maps to a verification method, or schedules building one. The
plan conforms to the architecture guidance. Every owed surface is planned. Where the plan is phased, the phases are
ordered and independently verifiable.

Select `drafted` when the plan is complete and ready for the cold plan-review gate.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run
`blizzard runner artifact create --name retrospective` with a few honest lines on stdin — what went well, what didn't,
and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.

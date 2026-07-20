# Plan — judgement

Assess the plan you just authored: every planned change maps to a verification method (or schedules building one), the phases are ordered and independently verifiable, the plan conforms to the architecture guidance, and every owed surface is a phase.

Select `drafted` when the plan is complete and ready for the cold plan-review gate.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner attach --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.

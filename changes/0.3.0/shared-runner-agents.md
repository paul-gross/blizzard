---
kind: changed
surfaces: [config, wire, board]
---

A runner's agents now form one count shared by every project it serves, instead of one cap per binding. Set it with
`[[runner]] agents`; unset, it is the sum of the bindings' `agents`, so an existing host keeps its total. A free agent
takes the next ready chunk of any bound project whose workspace has a free environment, in the hub's ready-queue order.
The matched peek accepts `projects`, and under `policy="hold"` an unusable chunk holds only its own project.
Registration carries the runner's `agents`, and the board shows agents in use over that count, with each binding line
showing how many agents work that project.

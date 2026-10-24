---
kind: changed
surfaces: [http-api, store]
upgrade-guide: hub-nodes-run-no-commands
---

A hub node no longer runs a `run:` command. The hub store's migration carries every stored hub node onto the action its
`run:` named, and minting a graph whose hub node names `run:` is refused, naming `action:`. A chunk standing at a node
whose command could not be carried escalates with cause `hub-action-missing`, and a pass answering a choice no edge
routes escalates with cause `unroutable-outcome`, rather than waiting. A hub node with no `poll_timeout` waits three
hours.

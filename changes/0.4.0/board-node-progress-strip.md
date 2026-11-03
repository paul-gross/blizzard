---
kind: added
surfaces: [board]
---

Running, waiting and needs-human cards, list rows and mobile cards now show a strip with one block per node of the
chunk's graph, start to finish: green where its last visit passed, red where it failed, dark where it never went, and
pulsing on the node it stands on. Their step now reads `graph/node`, and a held card adds its `host/runner`.

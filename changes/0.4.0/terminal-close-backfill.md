---
kind: fixed
surfaces: [store]
---

Upgrading enqueues outstanding work references on chunks that reached the reserved `done` terminal before terminal
transitions enqueued close decisions. The normal close drain processes these recovered intents, including skipping
sources without a closer.

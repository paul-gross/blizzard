---
kind: changed
surfaces: [store]
---

Delivered work with no bound closer records a terminal skipped closure instead of leaving a close intent pending. This
includes forge sources when `close_forge_writes_enabled` is false and legacy pending intents reached by the drain. The
chunk's `work-item-close-skipped` event identifies the skip; binding a closer later does not automatically retry it.

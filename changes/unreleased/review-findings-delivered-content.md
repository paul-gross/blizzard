---
kind: fixed
---

The `review-findings-delivered` asset that `record-findings` writes on a chunk is no longer empty. It lists each minted
finding by the review delta's `ref`, the hub finding id and the scope, or states that none were deferred, in the shape
the `review/findings-delivered-format` system artifact declares. Assets already written stay empty.

---
kind: changed
---

Hub background sweeps now name their tenant as `tenant_id` on every line written for that tenant. Per-tenant skip and
failure lines use `tenant_id` instead of `tenant`; hub-wide summary lines remain untagged.

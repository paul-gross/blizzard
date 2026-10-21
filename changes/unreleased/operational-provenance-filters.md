---
kind: added
surfaces: [http-api, cli]
---

The operational analytics datasets — step durations, spend by node, graph and chunk, and outcomes — now take `model`, `harness_id` and `harness_version` filters (`--model`, `--harness-id` and `--harness-version` on `blizzard hub analytics summary`), matching a lease attempt by the usage facts it recorded. A new `GET /api/analytics/spend/models` route and `summary spend-models` dataset roll spend up by model.

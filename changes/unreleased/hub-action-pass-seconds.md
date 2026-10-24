---
kind: added
surfaces: [config, http-api]
---

`blizzard-hub.toml` takes an optional `action_pass_seconds` key (default 300) bounding one pass of a hub node's action;
an overrun is cancelled and logged as `hub-action-overran`. The graph node view now carries a node's `action` and
`action_args`, which a node that names an action sets in place of `run:` steps.

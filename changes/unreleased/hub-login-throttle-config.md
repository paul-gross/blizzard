---
kind: added
surfaces: [config]
---

The hub file gains `[auth.login_throttle]`, tuning the per-client-address sign-in throttle: `enabled` (default `true`;
`false` admits every sign-in unthrottled and logs a warning at boot), `capacity` (default `10` tokens per address), and
`refill_seconds` (default `6` seconds per token). A hub whose file names none of them throttles exactly as before.

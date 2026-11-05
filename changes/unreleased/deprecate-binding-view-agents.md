---
kind: deprecated
surfaces: [http-api, wire]
removes-in: "0.6"
---

A runner binding's `agents` in the runner registry and registration responses is deprecated. It repeats the runner's
shared `agents` count (`0` where the runner sent none), and no client reads it. Read the runner's own `agents` instead.

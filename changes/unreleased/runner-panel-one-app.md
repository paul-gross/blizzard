---
kind: changed
surfaces: [board]
---

The runner panel is one app across a host's runners. Its tabs stay fixed, and a Runner and a Project filter narrow every
page instead of a runner switcher. The Board lays chunks out as the hub's board does — Needs human, In progress and Done
lanes or a list, cards tinted by project — and a chunk's detail lists every session it ran on this host. The Machine tab
is now Host: the host's subscription usage, one row per runner with its bindings, harness health, hub link, gates, state
and the chunks it holds, and the host's environments by workspace. A runner the hub's version window refuses reads
`VERSION REFUSED` on its hub link, beside the hub's version and the window it admits. Old `/runners/<id>/…` links
redirect to the same page filtered to that runner, and `/machine` to `/host`.

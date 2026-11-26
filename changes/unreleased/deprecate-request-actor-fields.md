---
kind: deprecated
surfaces: [http-api, cli]
removes-in: "0.6"
---

The `by` field on the hub's chunk, dependency, runner, host, cadence, routine and scope lifecycle requests,
`answered_by` on a question answer, and `resolved_by` on a decision resolution are deprecated. Each is still accepted
and ignored: the hub records the signed-in caller. Stop sending them.

The `--by` option of `blizzard hub chunk pause`, `resume`, `depend`, `release-dependency`, `stop`, `done`, `restart` and
`delete`; of `blizzard hub runner pause`, `resume`, `retire`, `reinstate` and `revoke-token`; of
`blizzard hub runner-host adopt`, `release`, `revoke-token` and `retire`; of `blizzard hub routine` and
`blizzard hub scope` `retire` and `enable`; of `blizzard hub question answer`; and of `blizzard hub decision resolve` is
deprecated too. It is still accepted, warns, and is ignored. Stop passing it.

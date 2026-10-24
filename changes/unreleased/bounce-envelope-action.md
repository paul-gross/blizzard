---
kind: added
surfaces: [http-api, board]
---

A `bounce-envelope` artifact names the hub action whose pass answered the bounce: `action` carries its `name`, the
`choice` it answered, and `log_artifact`, the `hub-log.<action>` asset holding the pass's log, `null` when the pass
logged nothing. The envelope's `detail` reads ``… did not land: `land` answered failure``. A poll-timeout bounce is the
hub's own judgement and writes `action: null`, and an envelope stored before this release carries no `action`. The board
shows the action as its own row on the bounce card.

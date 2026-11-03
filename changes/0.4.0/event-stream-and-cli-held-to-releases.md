---
kind: changed
surfaces: [event-stream, cli]
---

The event stream and the `blizzard hub` and `blizzard runner` CLI now carry the same compatibility promise as the HTTP
API. A release no longer removes a frame kind, a payload field, a command, an alias, an option or an argument without
first deprecating it, and adding a required option or argument is breaking. A consumer of the stream ignores a kind or
field it does not know, which is what keeps an added one additive.

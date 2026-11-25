---
kind: fixed
surfaces: [http-api, cli]
---

`blizzard hub graph mint` and `POST /api/graphs` now refuse graph values that used to mint and were then mangled or
dropped without a word: a list, map, or boolean where one text value belongs, an unrecognized key under a session's
`rotate:`, an empty `model` on a `graph:<name>` choice, an absolute or escaping `checks_cwd`, and a `prompt_addendum` on
a `graph:<name>` or `escalate` choice.

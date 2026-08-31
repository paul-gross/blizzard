# Propose — after a failed delivery

`deliver` failed to run rather than rejecting your artifacts: a missing env var, a failed request, or a failed marker
write, never a claim about the delta or the docket. Nothing about what you concluded is in question.

Confirm the `docket` asset still reads as you left it (`blizzard runner artifact get docket --content`) — redraft
nothing — then republish it unchanged with `blizzard runner artifact create --name docket` so this entry has its own
completion, and select `proposed` or `none` exactly as before to retry delivery.

**Loop bound.** Before retrying, read `blizzard runner chunk history`. If a `failure` transition has already left
`deliver` once for this chunk, do not retry again: this is an operational fault outside what a worker session can
repair. Escalate with `blizzard runner ask` instead so a human fixes the hub's delivery path.

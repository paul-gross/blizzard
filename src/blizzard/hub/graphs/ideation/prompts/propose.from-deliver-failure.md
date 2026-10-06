# Propose — after a failed delivery

`deliver` failed to run rather than rejecting your artifacts: a missing env var, a failed request, or a failed marker
write, never a claim about the docket or the delta. Nothing about what you concluded is in question.

Confirm the `docket` and `delta` assets still read as you left them
(`blizzard runner artifact get docket --content`, `blizzard runner artifact get delta --content`) — redraft nothing —
then republish both unchanged with `blizzard runner artifact create --name docket` and
`blizzard runner artifact create --name delta` so this entry has its own completion, and select `proposed` or `none`
exactly as before to retry delivery.

**Loop bound.** Before retrying, read `blizzard runner chunk history` and count the `failure` transitions out of
`deliver` for this chunk — the one that sent you here is included in the count. If exactly one appears, this is the
first failure: retry once. If two or more appear, a retry has already failed: do not retry again, because this is an
operational fault outside what a worker session can repair. Escalate with `blizzard runner ask` instead so a human fixes
the hub's delivery path.

---
kind: changed
---

The runner now asks each live lease's harness, about once a minute, whether the lease's current execution is still
running. Evidence the harness gives for the current spawn keeps the lease alive the same way a heartbeat does, so a long
tool call is not reaped while it is observed running. A worker observed running with no progress for 15 minutes raises
one `worker-no-progress` warning in the event feed, naming its activity and how long it has gone without progress; the
worker is never stopped for it. Claude Code workers now launch with `--output-format stream-json --verbose`, and a
recent write to the worker's own output counts as that evidence, so a Claude Code tool running past an hour is no longer
reaped while it runs; Claude Code older than 2.1.322 reports no evidence. Other harnesses report none yet, so their
leases are still reaped on heartbeats alone.

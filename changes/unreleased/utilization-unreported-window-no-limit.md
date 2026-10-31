---
kind: fixed
surfaces: [config]
---

A `[runner.throttle.utilization]` or `[runner.throttle.pace]` line no longer holds a runner indefinitely with
`window 5h not reported` on Anthropic's top plan, which reports `five_hour: null`. A named window the provider reports
as null is a limit the plan does not impose: the lever admits and reads `<window>: no limit`. A named window the sample
does not carry at all, such as a mistyped label or an entry that could not be parsed, still holds, as does a stale
sample.

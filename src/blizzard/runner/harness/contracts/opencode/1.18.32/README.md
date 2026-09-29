# OpenCode 1.18.32 live capture (supplement)

A sanitized capture of one real root session that spawned `task` children, exported with the
1.18.32 CLI. It carries no `manifest.json`, so it is not an admitted corpus: the 1.18.25 corpus
stays the reference. It pins the real `task` shape (`state.metadata.sessionId`, `input.subagent_type`,
`state.time`, `info.time.created`) the hand-authored 1.18.25 `child_session.json` cannot.

- `root_export.json` — the root's `opencode export`, trimmed to the messages holding `task` parts.
- `child_*.json` — two child exports on a different model than the root; `ses_f6070f70…` was
  continued through `input.task_id`, so both of its task windows hold steps.
- `run_generation_1.jsonl`, `run_generation_2.jsonl` — the root's `tool_use` / `step_finish`
  events for two invocations, in the shape `opencode run --format json` emits.

Prompt, output, text, reasoning, title and path content is replaced; ids, times, tokens, cost,
`providerID` / `modelID` and key structure are verbatim. The sessions were recorded by 1.18.30 and
exported by the 1.18.32 CLI.

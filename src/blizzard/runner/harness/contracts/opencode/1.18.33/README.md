# OpenCode 1.18.33 live capture (supplement)

A sanitized capture of one real root fleet session that read files, invoked a skill, and spawned
`task` children, recorded and exported with the 1.18.33 CLI. It carries no `manifest.json`, so it
is not an admitted corpus: the 1.18.25 corpus stays the reference. It pins the tool names and
input keys OpenCode actually emits for the analytics dialect: `read` / `filePath`,
`skill` / `name`, and `task` / `subagent_type`.

- `root_export.json` — the root's `opencode export`, trimmed to the messages holding `read`,
  `skill`, and `task` parts.

Prompt, output, text, reasoning, title and path content is replaced; tool names, input keys, the
`skill` name and the `task` `subagent_type` values, ids, times, tokens and key structure are verbatim.

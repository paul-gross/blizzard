# Authoring node prompts

Maintainer conventions for node-prompt trees — minted in this graph, the reference exemplar, and binding **every
packaged graph's** `prompts/` directory. A node prompt is injected into the worker's first turn and re-read by every
turn that follows, so frontloaded prose is the most expensive prose in the system — its cost is multiplied by the
session's whole call count. Write every prompt as a terse operating reference with depth reachable on demand, never as
an explanatory document:

- **A rule that reduces to a decision and a destination is a bullet, not a section.** State the test and where the
  result goes; leave the rationale out.
- **Standing prose a worker needs at most once is a pointer, not inline text.** Ship the long form as a graph-scoped
  artifact (like `docket.md`) or behind a command the worker runs on the branch that needs it, so the tokens land in one
  turn instead of every turn. A graph-scope pointer always names its fallback (`bzh:graph-artifact-pointer-fallback`).
- **A command surface is a cheat sheet** — a table of verb, purpose, and where to read more (usually the command's own
  `--help`) — never a paragraph per command.
- **A node prompt stays within 4,000 bytes.** The bar is a cost tripwire, not a structure rule: a prompt over it moves
  its excess behind a link read only on the branch that needs it, rather than growing a per-turn tax.

The runner's fleet-worker preamble tree (`src/blizzard/runner/harness/prompts/`) is bound the same way and inherits the
4,000-byte bar; `blizzard_preamble.md` itself is frontloaded into every worker session in every deployment, so it is
held within a tighter 2,367-byte bar — set just above the file's compressed size, so any growth there is a deliberate
act rather than drift. Both trees' bars are asserted by `tests/test_prompt_byte_bars.py` (`blizzard:unit-test`), so an
edit over a bar fails the suite rather than waiting on a hand-run `wc -c`.

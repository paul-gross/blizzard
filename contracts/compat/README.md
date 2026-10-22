# Compatibility allowances

`allowances.toml` records the breaks `blizzard-compat` admits against one supported release line because that line's
consumers survive them. Each entry is a reviewed fact about a shipped line, not an exemption for a surface: it names one
exact finding against one line and says why that line's releases survive it. The file is written by hand, never
generated, and is read from the working tree.

## An entry

```toml
[[allowance]]
line = "0.2"
surface = "http-api"
where = "WidgetState"
reason = "response enum gained value(s) ['paused']"
because = "0.2's only consumer of WidgetState, the runner CLI, compares it as a string and shows an unknown value raw (v0.2.0:src/blizzard/runner/cli/widgets.py:40-48)."
```

| Field     | Holds                                                                                                                    |
| --------- | ------------------------------------------------------------------------------------------------------------------------ |
| `line`    | The release line the finding is against, `MAJOR.MINOR` with no leading zeros, as a tag's line reads: `0.2` for `v0.2.1`. |
| `surface` | The finding's surface, exactly as `blizzard-compat --json` prints it.                                                    |
| `where`   | The finding's `where`, exactly.                                                                                          |
| `reason`  | The finding's `reason`, exactly.                                                                                         |
| `because` | Prose citing each consumer as `v<tag>:<path>:<line>`; `blizzard-context:/workflows/judging-a-break.md` owns its content. |

Every field is a non-empty string, and no other field or table is allowed. Two entries may not name the same finding.

## What an entry does

- **Admits its finding against its line's releases only.** Against whichever release of the line a run judges, the
  finding passes under the state `allowance`, printed with its `because` and the entry's line. It stays a finding:
  `--json` and `compat-breaks.json` carry it with an `allowance` field holding the `line` and the `because`, so a change
  note may still declare it breaking.
- **Only for a candidate on a later line.** A `--tag` candidate on a later line than the entry's admits it, that later
  line's patches included; a candidate on the entry's own line admits nothing new. A run with no `--line` judges an open
  `master`, which is above every line. A `--line release/X.Y` run admits no entry for `X.Y`.
- **Fails the run when stale,** in every mode: when the window no longer supports its line, or when the run judges the
  release of its line the window supports and the entry matches none of its findings. A `--line` run judges only that
  line's releases, so an in-window entry for another line does not fail it. A stale entry prints as `allowance: [BREAK]`
  and appears under the `allowances` key of `--json`, with its `line` and `problem`.

Remove an entry in the change that makes it stale: the floor moving past its line, or the finding going away. A patch
cut on the entry's line needs no change: the window then supports the patch, and the entry matches the same finding
against it.

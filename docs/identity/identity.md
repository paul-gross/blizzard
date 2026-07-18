# Blizzard visual identity

The mark is the **hub-flake**: a snowflake that is secretly an orchestration graph.
An amber hub node sits at the center, six snow-white spokes with branch chevrons fan
out, and a cyan agent node caps each tip — snowstorm and fleet-of-agents in one shape.

Every color is a token from the board's design system
(`web/projects/fleet/src/lib/design/tokens.css`), so the mark reads as native next to
the mission-control UI:

| Role | Token | Hex |
|------|-------|-----|
| Hub, halo, wordmark accent | `--amber` | `#f2b25c` |
| Agent nodes (spoke tips) | `--cyan` | `#5cd1e5` |
| Flake — spokes and chevrons | snow white (identity-only) | `#eef4fb` |
| Ground / favicon plate | `--bg` | `#060a12` |

## Files

| File | What it is |
|------|------------|
| [`logo-hubflake.svg`](./logo-hubflake.svg) | The primary mark, 64×64 viewBox. Use at ~40px and up. |
| [`favicon.svg`](./favicon.svg) | The mark at true proportions on a transparent background, 32×32 viewBox, stroke weights nudged up a hair for rasterization. Tuned for 48/32px; at 16px it dissolves toward dots — that sparseness is the character, not a defect. Also the small-header lockup mark. |
| [`logo-orbit.svg`](./logo-orbit.svg) | Explored variant: no chevrons, dashed fleet ring linking the agent nodes. Not adopted. |
| [`logo-drift.svg`](./logo-drift.svg) | Explored variant: wind-driven task streams. Not adopted. |
| [`blizzard-identity.html`](./blizzard-identity.html) | The full self-contained proposal page — all variants, wordmark lockups, favicon size strip, palette. Open directly in a browser. |

## Wordmark

Lowercase `blizzard` in the board's `--mono` stack, set beside the mark. The optional
two-tone split colors `bliz` amber and `zard` in `--text`; a single-color wordmark is
equally valid.

# Identity

The blizzard mark is the **hub-flake** — a snowflake that is secretly an orchestration graph: an amber hub node at the
center, six snow-white spokes with branch chevrons, and a cyan agent node capping each tip.

## Files

- [`logo-hubflake.svg`](./logo-hubflake.svg) is the primary mark — 64×64 viewBox, for dark grounds, used at roughly 40px
  and up.
- [`logo-hubflake-light.svg`](./logo-hubflake-light.svg) is the same mark for light grounds — the flake inverts to
  `--bg`, the halo lifts to opacity 0.7, and amber and cyan keep their exact values. Pair the two behind `<picture>` +
  `prefers-color-scheme` where the ground follows the reader's theme.
- [`favicon.svg`](./favicon.svg) is the mark on a transparent background — 32×32 viewBox, strokes nudged slightly
  heavier for rasterization, tuned for 48/32px; at 16px it dissolves toward dots, a deliberate sparseness. It doubles as
  the small-header lockup mark.

## Palette

Hub, halo, and wordmark accent are `--amber` `#f2b25c`; the agent nodes at the spoke tips are `--cyan` `#5cd1e5`; the
flake's spokes and chevrons are identity-only snow white `#eef4fb`; the ground is `--bg` `#060a12`. Every mark color
except the snow white is a token from the board's design system
([`web/projects/fleet/src/lib/design/tokens.css`](../../web/projects/fleet/src/lib/design/tokens.css)), so the mark
reads as native beside the board UI.

## Wordmark

The wordmark is lowercase `blizzard` in the board's `--mono` stack, set beside the mark. An optional two-tone split
colors `bliz` `--amber` and `zard` `--text`; single-color is equally valid.

## Variants and proposal

[`logo-orbit.svg`](./logo-orbit.svg) (dashed fleet ring, no chevrons) and [`logo-drift.svg`](./logo-drift.svg)
(wind-driven task streams) are unadopted explored variants. [`blizzard-identity.html`](./blizzard-identity.html) is the
self-contained proposal page — all variants, wordmark lockups, favicon size strip, palette.

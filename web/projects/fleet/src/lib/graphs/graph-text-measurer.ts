import { InjectionToken } from '@angular/core';

import type { TextMeasurer } from './graph-layout';

/**
 * The production {@link TextMeasurer} for the graph diagram — canvas `measureText`
 * against the type `graph-diagram.ts` actually draws with.
 *
 * Its own module rather than part of the component: it is the one piece of the diagram
 * that is neither template nor layout, it is the only piece a browser is *required* to
 * exercise (jsdom has no canvas backend, so the component specs stub it), and keeping it
 * here holds `graph-diagram.ts` under the `web:structural-gate` line cap.
 *
 * **This file and `graph-diagram.ts`'s `styles` must change together.** Every font
 * string and tracking value below mirrors a `.node-name` / `.node-badge` / `.node-meta` /
 * `.edge-label` rule there; when they drift, boxes size to type the SVG does not draw,
 * which is exactly the class of bug issue #157 was. The e2e
 * `test_diagram_geometry_matches_the_rendered_text` is the guard that catches the drift.
 */

/**
 * Resolves the concrete monospace family the SVG actually renders in — the `--mono`
 * custom property from `tokens.css`, read off the document element **once**.
 *
 * Canvas font strings are CSS `font` *shorthand values*, not declarations, so they are
 * resolved against no element and `var()` never substitutes: assigning
 * `'400 11px var(--mono, monospace)'` is silently rejected and `ctx.font` keeps its
 * `10px sans-serif` default, measuring every string far narrower than it renders
 * (issue #157 — meta lines overflowed their boxes by ~80%). The property has to be
 * dereferenced here and spliced into the shorthand literally.
 *
 * Falls back to bare `monospace` when the property is unset or the spliced shorthand
 * is itself rejected — detected by assigning it and seeing `ctx.font` not move off a
 * known-good probe, since canvas rejects an invalid font by leaving the old value in
 * place rather than throwing.
 */
function resolveMonoFamily(ctx: CanvasRenderingContext2D | null): string {
  let family: string;
  try {
    family = getComputedStyle(document.documentElement).getPropertyValue('--mono').trim();
  } catch {
    family = '';
  }
  if (family === '') return 'monospace';
  if (ctx === null) return family;
  ctx.font = '400 11px monospace';
  const probe = ctx.font;
  ctx.font = `400 11px ${family}`;
  // Unchanged means either "rejected" or "resolves identically to monospace" — the
  // fallback is the right answer for both.
  return ctx.font === probe ? 'monospace' : family;
}

/** Per-kind tracking, in px, mirroring the `letter-spacing` declarations in the
 * component's styles. Canvas ignores tracking unless it is asked for explicitly —
 * `measureText` applies `ctx.letterSpacing`, which defaults to `0px` — so a kind whose
 * CSS rule sets `letter-spacing` measures short by this much per character unless it is
 * named here. Only `.node-badge` sets one today (`0.06em` at 10px = 0.6px). */
const LETTER_SPACING_PX: Record<string, number> = { name: 0, badge: 0.06 * 10, meta: 0, label: 0 };

/**
 * Canvas-backed {@link TextMeasurer} — measures a string's rendered pixel width for
 * real, per the spike's "measured text, not char-count estimation" constraint. Falls
 * back to a fixed per-character estimate only if the runtime has no working 2D canvas
 * context (unsupported in practice for a real browser; this is the defensive edge, not
 * the intended path — jsdom under Vitest is exactly this edge, which is why component
 * specs stub the layout directly rather than relying on this measurer's output).
 */
function createCanvasTextMeasurer(): TextMeasurer {
  const canvas = document.createElement('canvas');
  // jsdom (the unit-test DOM) has no canvas backend and throws rather than
  // returning null — guard the same way as an unsupported runtime.
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext('2d');
  } catch {
    ctx = null;
  }
  const mono = resolveMonoFamily(ctx);
  const fonts: Record<string, string> = {
    name: `600 13px ${mono}`,
    badge: `700 10px ${mono}`,
    meta: `400 11px ${mono}`,
    label: `400 11px ${mono}`,
  };
  // `letterSpacing` is Chrome 99+ / Safari 17.4+ / Firefox 127+. Where the engine has
  // it, let it apply the tracking — its own model is by definition the one the SVG
  // renders with. Where it does not, add the tracking arithmetically instead: uniform
  // per character, trailing included, which is what CSS does for the ASCII executor
  // names this kind ever carries.
  const hasLetterSpacing = ctx !== null && 'letterSpacing' in ctx;
  const fallbackCharWidth: Record<string, number> = { name: 8, badge: 7, meta: 6, label: 6.5 };
  return (text, kind) => {
    if (ctx) {
      ctx.font = fonts[kind];
      const tracking = LETTER_SPACING_PX[kind];
      if (hasLetterSpacing) {
        ctx.letterSpacing = `${tracking}px`;
        return ctx.measureText(text).width;
      }
      return ctx.measureText(text).width + tracking * text.length;
    }
    return text.length * fallbackCharWidth[kind];
  };
}

/** Text-measurer seam, overridable alongside `GRAPH_LAYOUT` for deterministic specs;
 * production default is {@link createCanvasTextMeasurer}. */
export const GRAPH_TEXT_MEASURER = new InjectionToken<TextMeasurer>('fleet.GRAPH_TEXT_MEASURER', {
  providedIn: 'root',
  factory: () => createCanvasTextMeasurer(),
});

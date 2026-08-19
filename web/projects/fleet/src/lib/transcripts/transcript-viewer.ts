import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { formatAbsolute, formatLocalClockWithDay, type LocalClockWithDay } from '../when';
import type { TranscriptSidechain, TranscriptTool, TranscriptTurn } from './transcript-turn';

/** The `<summary>` line's own cap (`review:F8`) — clamps visually via CSS too
 * (`.tc-input`'s `text-overflow: ellipsis`), but a tool call's structured input can run
 * to megabytes; capping the string bounds the *retained* preview and DOM node, not the
 * `JSON.stringify` cost, which still runs once per turn —
 * {@link TranscriptViewer.inputPreviewsByTool} is what keeps that a once-per-turns-change
 * cost rather than a once-per-change-detection-pass one (`review:F6`). */
const MAX_INPUT_PREVIEW_CHARS = 300;

/** {@link TranscriptViewer.inputPreviewsByTool}'s per-tool computation, a free function
 * so the memoizing `computed()` calls it once per turn instead of the template calling it
 * once per change-detection pass (`review:F6`). */
function computeInputPreview(tool: TranscriptTool): string {
  const raw = tool.input_shape === 'object' ? JSON.stringify(tool.input) : (tool.input_unparsed ?? '');
  return raw.length > MAX_INPUT_PREVIEW_CHARS ? `${raw.slice(0, MAX_INPUT_PREVIEW_CHARS)}…` : raw;
}

/** One turn's recursive "open standalone" address (blizzard#248 D7, `review:F3`) —
 * emitted instead of a bare {@link TranscriptTurn} so a sidechain nested more than one
 * level deep carries the full `SidechainPath` (`./transcript-sidechain-path`) down to it,
 * not just its own locally-indexed turn. `turn` is the turn that owns the addressed
 * sidechain — a `tool` turn for a nested one, a `sidechain` turn for an unlinked one. */
export interface SidechainOpenEvent {
  readonly turn: TranscriptTurn;
  readonly path: readonly number[];
}

/**
 * The shared, presentational turn list (blizzard#248 D3/D4) — one component both the
 * runner's local panel (`local-panel/src/lib/transcript-panel.ts`) and the hub's chunk
 * Transcripts tab render, over the structural {@link TranscriptTurn} shape. Injects
 * nothing and owns no query; a container passes `turns` and this component only ever
 * renders what it is given (`bzh:frontend-container-presentational`).
 *
 * Four turn kinds render distinctly:
 * - `env`/`asst` — plain text, as `local-transcript-panel` always rendered them.
 * - `tool` — a `<details>` card naming the call, its structured input, and its output
 *   (or a running placeholder while `tool.output` is still `null`); a tool call that
 *   spawned a sidechain nests this same component recursively inside the card, behind
 *   its own open-standalone control (blizzard#248 D7 — "nested and standalone
 *   sidechains are one component", read both nested-in-context and standalone;
 *   `review:F3`).
 * - `thinking` — collapsed by default, expanding in place; a redacted turn
 *   (`thinking_redacted`) shows a presence placeholder instead of prose.
 * - `sidechain` — an *unlinked* sidechain (blizzard#248 D7: no spawning tool call to
 *   nest under) renders as its own top-level entry, recursing the same way a nested one
 *   does.
 *
 * `openStandalone` always emits a {@link SidechainOpenEvent} — the owning turn plus the
 * full `SidechainPath` down to it — forwarded through every recursive instantiation, each
 * level prepending its own spawning turn's index as it re-emits outward, so a bubbled
 * emission from a sidechain nested arbitrarily deep still names it uniquely rather than
 * just its own locally-indexed identity (`review:F3`).
 *
 * `turnClockInfo`/`turnAbsolute` render a turn's `timestamp` in the viewer's own local
 * zone (issue #136, `bzh:utc-instants`) — unchanged from `local-transcript-panel`'s own
 * copy, which this component now owns instead.
 */
@Component({
  selector: 'fleet-transcript-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  templateUrl: './transcript-viewer.html',
  styleUrl: './transcript-viewer.css',
})
export class TranscriptViewer {
  /** The turns to render, in order — this component never fetches or filters them. */
  readonly turns = input.required<readonly TranscriptTurn[]>();

  /** Emitted with a {@link SidechainOpenEvent} when the operator asks to view a sidechain
   * standalone (blizzard#248 D7, `review:F3`). This component always renders the
   * sidechain inline too; a container with no standalone concept (the runner's local
   * panel) needs no listener at all; the hub's Transcripts tab turns this into a
   * URL-held selection. */
  readonly openStandalone = output<SidechainOpenEvent>();

  /** A turn's own open-standalone button, clicked directly — its path is just its own
   * index; {@link forwardOpenStandalone} prepends further indices as this bubbles out
   * through any enclosing viewer (`review:F3`). */
  protected emitOpenStandalone(turn: TranscriptTurn): void {
    this.openStandalone.emit({ turn, path: [turn.index] });
  }

  /** Forward a nested viewer's own emission outward, prepending `prefixTurn`'s index —
   * the turn that spawned that nested viewer — onto its path (`review:F3`). Without
   * this, an emission from a sidechain nested two or more levels deep would carry only
   * its own innermost index, which a different turn at any ancestor level can share. */
  protected forwardOpenStandalone(prefixTurn: TranscriptTurn, event: SidechainOpenEvent): void {
    this.openStandalone.emit({ turn: event.turn, path: [prefixTurn.index, ...event.path] });
  }

  /** {@link inputPreview}'s memoized backing (`review:F6`) — one `JSON.stringify` per
   * tool turn, recomputed only when {@link turns} changes, not on every change-detection
   * pass the way computing it directly in the template-invoked `inputPreview` did. */
  private readonly inputPreviewsByTool = computed<Map<TranscriptTool, string>>(() => {
    const previews = new Map<TranscriptTool, string>();
    for (const turn of this.turns()) {
      if (turn.tool !== null) previews.set(turn.tool, computeInputPreview(turn.tool));
    }
    return previews;
  });

  protected inputPreview(tool: TranscriptTool | null): string {
    if (tool === null) return '';
    return this.inputPreviewsByTool().get(tool) ?? '';
  }

  protected sidechainLabel(sidechain: TranscriptSidechain): string {
    const who = sidechain.agent_type ?? sidechain.agent_id ?? 'subagent';
    return sidechain.link === 'unlinked' ? `${who} · unlinked` : who;
  }

  /**
   * A turn's browser-local `HH:MM:SS` plus day context, or `null` when
   * absent/unparsable — the template's time cell falls back to `—`. Moved from
   * `local-transcript-panel` unchanged (issue #136, `bzh:utc-instants`): the wire and
   * the runner store stay UTC end to end, this is display decoration over that instant.
   */
  protected turnClockInfo(iso: string | null): LocalClockWithDay | null {
    return formatLocalClockWithDay(iso);
  }

  /** {@link turnClockInfo}'s full local date + time, for the stamp's hover tooltip. */
  protected turnAbsolute(iso: string | null): string {
    return formatAbsolute(iso);
  }
}

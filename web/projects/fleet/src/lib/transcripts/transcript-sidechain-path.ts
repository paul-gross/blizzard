import type { TranscriptSidechain, TranscriptTurn } from './transcript-turn';

/**
 * A sidechain's address within one segment (blizzard#248 D7, `review:F3`) — the
 * turn-index path from the segment's top-level turns down to the turn that owns the
 * addressed sidechain, one entry per nesting level. A scalar turn index cannot address a
 * sidechain nested more than one level deep: each sidechain's own turns index
 * independently from 0, so a turn at index 0 inside a nested conversation shares its
 * index with an unrelated top-level turn. The path disambiguates by naming every
 * ancestor's own index, top-down, the way a filesystem path names every directory.
 */
export type SidechainPath = readonly number[];

/** {@link SidechainPath} on the wire (a URL query param, `chunk-detail-selection.ts`'s
 * `?sidechain`) — dot-joined indices, e.g. `"1.0"` for the sidechain owned by the turn at
 * index 0 within the top-level turn-index-1 sidechain's own turns. */
export function encodeSidechainPath(path: SidechainPath): string {
  return path.join('.');
}

/** The inverse of {@link encodeSidechainPath}. `null`/empty parses to `[]` — no path
 * selected. Unvalidated, the same stance `chunk-detail-selection.ts` takes on every raw
 * param it carries: a garbage segment resolves to `NaN`, which {@link resolveSidechainByPath}
 * then simply fails to match, the same as any other path naming nothing. */
export function parseSidechainPath(raw: string | null): SidechainPath {
  if (raw === null || raw === '') return [];
  return raw.split('.').map(Number);
}

/**
 * Walk a {@link SidechainPath} from a segment's top-level turns down to the sidechain it
 * addresses, or `null` if any step of the path names nothing (`review:F3`). Each step
 * finds the turn at that level carrying the given index *and* a non-null sidechain — the
 * same shape a nested (`"tool"`) or unlinked (`"sidechain"`) turn both carry — then
 * descends into that sidechain's own turns for the next step.
 */
export function resolveSidechainByPath(
  turns: readonly TranscriptTurn[],
  path: SidechainPath,
): TranscriptSidechain | null {
  if (path.length === 0) return null;
  let currentTurns = turns;
  let sidechain: TranscriptSidechain | null = null;
  for (const index of path) {
    const turn = currentTurns.find((t) => t.index === index && t.sidechain !== null);
    if (turn === undefined || turn.sidechain === null) return null;
    sidechain = turn.sidechain;
    currentTurns = sidechain.turns;
  }
  return sidechain;
}

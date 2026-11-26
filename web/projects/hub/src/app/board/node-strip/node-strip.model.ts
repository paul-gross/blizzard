import { type LaneStatus, domainStatusLane } from 'fleet/core';
import { type Tone, toneColor, toneDimColor } from 'fleet/kit';
import type { GraphProgressNode, GraphProgressState } from '../../core/chunks.domain';

/** One drawn block: a node of the chunk's graph and how the chunk's visits left it — the hub's
 * `ChunkSummary.graph_progress` entry. */
export type NodeBlock = GraphProgressNode;

/** What a block draws as. */
export type BlockState = GraphProgressState['kind'];

/** The state displayed in the tooltip, including an unknown server value. */
export function blockStateLabel(state: GraphProgressState): string {
  return state.kind === 'unknown' ? state.raw : state.kind;
}

/** The tone each block state draws in; `unvisited` has none and keeps the strip's dark resting block. The
 * `spawning` and `needs` mappings are colour matches, not meaning matches: retuning either tone retunes the strip. */
export const BLOCK_TONE: Record<BlockState, Tone | null> = {
  passed: 'done',
  failed: 'needs',
  current: 'spawning',
  unvisited: null,
  unknown: 'idle',
};

/** A block's fill colour: its tone's, or null for the resting block. */
export function blockFill(state: BlockState): string | null {
  const tone = BLOCK_TONE[state];
  return tone === null ? null : toneColor(tone);
}

/** A block's border colour: its tone's dim companion, or null for the resting block. */
export function blockEdge(state: BlockState): string | null {
  const tone = BLOCK_TONE[state];
  return tone === null ? null : toneDimColor(tone);
}

/** Blocks per group — the strip breaks into tens, as a subscription's window blocks do. */
export const NODE_STRIP_GROUP = 10;

/** The blocks broken into groups of {@link NODE_STRIP_GROUP}. */
export function groupBlocks(blocks: readonly NodeBlock[]): readonly (readonly NodeBlock[])[] {
  const groups: NodeBlock[][] = [];
  for (let i = 0; i < blocks.length; i += NODE_STRIP_GROUP) groups.push(blocks.slice(i, i + NODE_STRIP_GROUP));
  return groups;
}

/** Whether a chunk carries the strip: only in the lanes where it is mid-graph — RUNNING, WAIT/HUMAN, NEEDS HUMAN. */
export function showsNodeStrip(chunk: { readonly laneStatus: LaneStatus; readonly terminal: boolean }): boolean {
  const lane = domainStatusLane(chunk.laneStatus.kind);
  return !chunk.terminal && (lane === 'running' || lane === 'waiting' || lane === 'needs');
}

/** Past twenty blocks the strip packs tighter, so each still reads. */
export function isDenseStrip(groups: readonly (readonly NodeBlock[])[]): boolean {
  return groups.reduce((n, g) => n + g.length, 0) > 20;
}

import { type ChunkStatus, hubApi, STATUS_LANE } from 'fleet';

/** One drawn block: a node of the chunk's graph and how the chunk's visits left it — the hub's
 * `ChunkSummary.graph_progress` entry. */
export type NodeBlock = hubApi.GraphProgressNodeView;

/** What a block draws as: a known {@link hubApi.GraphProgressState}, or `unknown` for a wire state this build does not know. */
export type BlockState = hubApi.GraphProgressState | 'unknown';

const KNOWN_STATES: ReadonlySet<string> = new Set(
  Object.values(hubApi.GraphProgressState),
);

/** A wire state folded for drawing: a known state passes through, anything else is `unknown`, so it never reads as `unvisited`. */
export function blockState(state: string): BlockState {
  return KNOWN_STATES.has(state)
    ? (state as hubApi.GraphProgressState)
    : 'unknown';
}

/** Blocks per group — the strip breaks into tens, as a subscription's window blocks do. */
export const NODE_STRIP_GROUP = 10;

/** The blocks broken into groups of {@link NODE_STRIP_GROUP}. */
export function groupBlocks(
  blocks: readonly NodeBlock[],
): readonly (readonly NodeBlock[])[] {
  const groups: NodeBlock[][] = [];
  for (let i = 0; i < blocks.length; i += NODE_STRIP_GROUP)
    groups.push(blocks.slice(i, i + NODE_STRIP_GROUP));
  return groups;
}

/** Whether a chunk carries the strip: only in the lanes where it is mid-graph — RUNNING, WAIT/HUMAN, NEEDS HUMAN. */
export function showsNodeStrip(chunk: {
  readonly status: ChunkStatus;
  readonly terminal: boolean;
}): boolean {
  const lane = STATUS_LANE(chunk.status);
  return (
    !chunk.terminal &&
    (lane === 'running' || lane === 'waiting' || lane === 'needs')
  );
}

/** Past twenty blocks the strip packs tighter, so each still reads. */
export function isDenseStrip(
  groups: readonly (readonly NodeBlock[])[],
): boolean {
  return groups.reduce((n, g) => n + g.length, 0) > 20;
}

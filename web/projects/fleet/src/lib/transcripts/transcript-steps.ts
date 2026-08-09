import type { TranscriptSegmentIndexEntry, TransitionView } from '../api/hub';

/** The reserved terminal node id (`review:F2`) — the domain's `RESERVED_TERMINAL`
 * (`src/blizzard/hub/domain/graph.py`). Duplicated here (not a backend import) since
 * the wire model carries `current_node_id` as a plain string, not a discriminated
 * value. A completed chunk's `current_node_id()` is this terminal — never a step that
 * actually ran, so it names no in-flight step to append. */
const DONE_TERMINAL = 'done';

/**
 * One node-history step's transcript-segment group (blizzard#248 D5) — joined to
 * `ChunkDetail.history` by `(node_id, epoch)`: a {@link TransitionView}'s own
 * `from_node_id`/`epoch` name the step that *produced* it (the node that ran and then
 * transitioned out), the same pair a segment's `node_id`/`epoch` carries — not
 * `to_node_id`, which names where the chunk arrived next, a different step's own pair.
 */
export interface TranscriptStep {
  /** `${nodeId}:${epoch}` — stable across renders, used to track the open step. */
  readonly key: string;
  readonly nodeId: string | null;
  readonly nodeName: string | null;
  readonly epoch: number | null;
  /** Whether this is the chunk's in-flight step — no {@link TransitionView} names it
   * yet, since it has not transitioned out. */
  readonly current: boolean;
  /** `false` for a segment group whose `(node_id, epoch)` matched no history row and
   * isn't the in-flight step (D5's "must not silently hide conversation" case). */
  readonly matched: boolean;
  /** This step's segments, ordered by `spawn_generation` — resume-seam links (D6) are
   * this array's own adjacent indices, not a separate field. */
  readonly segments: readonly TranscriptSegmentIndexEntry[];
}

function bySpawnGeneration(entries: readonly TranscriptSegmentIndexEntry[]): TranscriptSegmentIndexEntry[] {
  return [...entries].sort((a, b) => a.spawn_generation - b.spawn_generation);
}

/**
 * Group a chunk's transcript segments into one entry per node-history step (D5), plus
 * the in-flight step and any segment group history doesn't name — a pure function over
 * the segment index and the chunk's own history/current-step fields, unit-testable
 * without a client.
 */
export function deriveTranscriptSteps(
  segments: readonly TranscriptSegmentIndexEntry[],
  history: readonly TransitionView[],
  current: { nodeId: string | null; nodeName: string | null; epoch: number | null },
): TranscriptStep[] {
  const bySegmentStep = new Map<string, TranscriptSegmentIndexEntry[]>();
  for (const segment of segments) {
    const key = `${segment.node_id}:${segment.epoch}`;
    const group = bySegmentStep.get(key);
    if (group) group.push(segment);
    else bySegmentStep.set(key, [segment]);
  }

  const steps: TranscriptStep[] = [];
  const claimed = new Set<string>();

  for (const transition of history) {
    if (transition.from_node_id === null) continue; // the graph's own entry step has no "from"
    const key = `${transition.from_node_id}:${transition.epoch}`;
    if (claimed.has(key)) continue; // a step that bounced back and forward again names one key once
    claimed.add(key);
    steps.push({
      key,
      nodeId: transition.from_node_id,
      nodeName: transition.from_node_name ?? null,
      epoch: transition.epoch,
      current: false,
      matched: true,
      segments: bySpawnGeneration(bySegmentStep.get(key) ?? []),
    });
  }

  if (current.nodeId !== null && current.nodeId !== DONE_TERMINAL && current.epoch !== null) {
    const key = `${current.nodeId}:${current.epoch}`;
    if (!claimed.has(key)) {
      claimed.add(key);
      steps.push({
        key,
        nodeId: current.nodeId,
        nodeName: current.nodeName,
        epoch: current.epoch,
        current: true,
        matched: true,
        segments: bySpawnGeneration(bySegmentStep.get(key) ?? []),
      });
    }
  }

  for (const [key, group] of bySegmentStep) {
    if (claimed.has(key)) continue;
    steps.push({
      key,
      nodeId: group[0].node_id,
      nodeName: null,
      epoch: group[0].epoch,
      current: false,
      matched: false,
      segments: bySpawnGeneration(group),
    });
  }

  return steps;
}

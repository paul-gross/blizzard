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
  const claimedEpochs = new Set<number>();

  for (const transition of history) {
    if (!transition.from_node_id) continue; // the graph's own entry step has no "from" — matches chunk-timeline.ts's own guard
    const key = `${transition.from_node_id}:${transition.epoch}`;
    claimedEpochs.add(transition.epoch);
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

  // Epochs are chunk-globally unique (minted once, at spawn), so an epoch a history
  // row already claimed can never legitimately belong to the in-flight step too — it
  // means a transition has already landed and `current_node_id` has moved on while
  // `latest_epoch` (minted only at the *next* lease's spawn) hasn't caught up yet
  // (`review:F3`). Suppressing the step here, rather than rendering it with a stale
  // epoch, avoids a false `<next node> · epoch <previous epoch>` "in progress" step.
  if (
    current.nodeId !== null &&
    current.nodeId !== DONE_TERMINAL &&
    current.epoch !== null &&
    !claimedEpochs.has(current.epoch)
  ) {
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

/** The open segment's resume-seam links (blizzard#248 D6), both derived from the same
 * ordering: {@link TranscriptStep.segments} is already sorted by `spawn_generation`, so
 * the segment immediately before/after the open one in its own step's array *is* the
 * continued-from/continues-in link — no separate field carries either direction. */
export interface SegmentSeams {
  readonly continuedFrom: TranscriptSegmentIndexEntry | null;
  readonly continuesIn: TranscriptSegmentIndexEntry | null;
}

const NO_SEAMS: SegmentSeams = { continuedFrom: null, continuesIn: null };

/**
 * Resolve one open segment's resume-seam links against `steps` (D5's own groups) — a
 * pure function over `(steps, segmentId)`, unit-testable without a mounted component
 * (`review:F11`), the same shape `deriveTranscriptSteps` already gives the step
 * derivation itself.
 */
export function resolveSegmentSeams(
  steps: readonly TranscriptStep[],
  segmentId: string | null,
): SegmentSeams {
  if (segmentId === null) return NO_SEAMS;
  for (const step of steps) {
    const index = step.segments.findIndex((s) => s.segment_id === segmentId);
    if (index === -1) continue;
    return {
      continuedFrom: index === 0 ? null : step.segments[index - 1],
      continuesIn: step.segments[index + 1] ?? null,
    };
  }
  return NO_SEAMS;
}

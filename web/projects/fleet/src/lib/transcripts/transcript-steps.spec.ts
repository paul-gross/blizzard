import type { TranscriptSegmentIndexEntry, TransitionView } from '../api/hub';
import { deriveTranscriptSteps } from './transcript-steps';

function segment(overrides: Partial<TranscriptSegmentIndexEntry> = {}): TranscriptSegmentIndexEntry {
  return {
    segment_id: 'seg-1',
    node_id: 'build',
    epoch: 2,
    spawn_generation: 0,
    turn_range_start: 0,
    turn_range_end: 10,
    final: true,
    truncated: false,
    byte_count: 100,
    normalizer_version: 'v1',
    harness_version: null,
    received_at: '2026-08-09T00:00:00+00:00',
    ...overrides,
  };
}

function transition(overrides: Partial<TransitionView> = {}): TransitionView {
  return {
    from_node_id: 'build',
    from_node_name: 'Build',
    to_node_id: 'review',
    to_node_name: 'Review',
    choice_name: 'pass',
    epoch: 2,
    recorded_at: '2026-08-09T00:00:00+00:00',
    graph_id: null,
    graph_name: null,
    ...overrides,
  };
}

describe('deriveTranscriptSteps', () => {
  it('groups segments under the history step that shares their (node_id, epoch)', () => {
    const steps = deriveTranscriptSteps(
      [segment({ segment_id: 'a', node_id: 'build', epoch: 2 })],
      [transition({ from_node_id: 'build', epoch: 2 })],
      { nodeId: null, nodeName: null, epoch: null },
    );

    expect(steps).toHaveLength(1);
    expect(steps[0].key).toBe('build:2');
    expect(steps[0].matched).toBe(true);
    expect(steps[0].current).toBe(false);
    expect(steps[0].segments.map((s) => s.segment_id)).toEqual(['a']);
  });

  it('lists one group per history entry, even one with zero segments', () => {
    const steps = deriveTranscriptSteps(
      [],
      [
        transition({ from_node_id: 'plan', epoch: 1, from_node_name: 'Plan' }),
        transition({ from_node_id: 'review', epoch: 2, from_node_name: 'Review' }),
      ],
      { nodeId: null, nodeName: null, epoch: null },
    );

    expect(steps.map((s) => s.key)).toEqual(['plan:1', 'review:2']);
    expect(steps.every((s) => s.segments.length === 0)).toBe(true);
  });

  it('appends the in-flight current step when it is not already covered by history', () => {
    const steps = deriveTranscriptSteps(
      [segment({ segment_id: 'a', node_id: 'build', epoch: 3 })],
      [transition({ from_node_id: 'plan', epoch: 1 })],
      { nodeId: 'build', nodeName: 'Build', epoch: 3 },
    );

    expect(steps.map((s) => s.key)).toEqual(['plan:1', 'build:3']);
    expect(steps[1].current).toBe(true);
    expect(steps[1].matched).toBe(true);
    expect(steps[1].segments.map((s) => s.segment_id)).toEqual(['a']);
  });

  it('buckets a segment matching no history row into its own unmatched step', () => {
    const steps = deriveTranscriptSteps(
      [segment({ segment_id: 'orphan', node_id: 'ghost', epoch: 9 })],
      [transition({ from_node_id: 'build', epoch: 2 })],
      { nodeId: null, nodeName: null, epoch: null },
    );

    expect(steps).toHaveLength(2);
    const unmatched = steps.find((s) => s.key === 'ghost:9');
    expect(unmatched?.matched).toBe(false);
    expect(unmatched?.nodeName).toBeNull();
    expect(unmatched?.segments.map((s) => s.segment_id)).toEqual(['orphan']);
  });

  it('orders a multi-segment step by spawn_generation, the resume-seam sequence (D6)', () => {
    const steps = deriveTranscriptSteps(
      [
        segment({ segment_id: 'second', node_id: 'build', epoch: 2, spawn_generation: 1 }),
        segment({ segment_id: 'first', node_id: 'build', epoch: 2, spawn_generation: 0 }),
      ],
      [transition({ from_node_id: 'build', epoch: 2 })],
      { nodeId: null, nodeName: null, epoch: null },
    );

    expect(steps[0].segments.map((s) => s.segment_id)).toEqual(['first', 'second']);
  });

  it('does not append a phantom in-flight step for a completed chunk\'s reserved "done" terminal (review:F2)', () => {
    const steps = deriveTranscriptSteps(
      [segment({ segment_id: 'a', node_id: 'build', epoch: 2 })],
      [transition({ from_node_id: 'build', epoch: 2, to_node_id: 'done' })],
      { nodeId: 'done', nodeName: null, epoch: 2 },
    );

    expect(steps.map((s) => s.key)).toEqual(['build:2']);
    expect(steps.every((s) => !s.current)).toBe(true);
  });

  it('a single-segment step has no adjacent segment on either side', () => {
    const steps = deriveTranscriptSteps(
      [segment({ segment_id: 'only', node_id: 'build', epoch: 2, spawn_generation: 0 })],
      [transition({ from_node_id: 'build', epoch: 2 })],
      { nodeId: null, nodeName: null, epoch: null },
    );

    expect(steps[0].segments).toHaveLength(1);
  });
});

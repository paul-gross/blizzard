import { effectiveSegmentId, segmentFinal, stepSegments } from './transcript-selection.model';

const SELECTION = { nodeId: 'nd_build', epoch: 1 };

describe('segmentFinal', () => {
  const segments = [
    { segment_id: 'seg_a', final: true },
    { segment_id: 'seg_b', final: false },
  ];

  it('is null while the index is still pending', () => {
    expect(segmentFinal(true, segments, 'seg_a')).toBeNull();
  });

  it("reads the listed segment's own final once the index resolves", () => {
    expect(segmentFinal(false, segments, 'seg_a')).toBe(true);
    expect(segmentFinal(false, segments, 'seg_b')).toBe(false);
  });

  it('falls to false, the still-live placement, for a segment the index does not list', () => {
    expect(segmentFinal(false, segments, 'seg_missing')).toBe(false);
    expect(segmentFinal(false, segments, null)).toBe(false);
    expect(segmentFinal(false, [], 'seg_a')).toBe(false);
  });
});

describe('effectiveSegmentId', () => {
  const segments = [{ segment_id: 'seg_a' }, { segment_id: 'seg_b' }];

  it('keeps a pick that names one of the segments', () => {
    expect(effectiveSegmentId(segments, 'seg_b')).toBe('seg_b');
  });

  it("falls back to the first segment when the pick names none of them (a stale pick from another step)", () => {
    expect(effectiveSegmentId(segments, 'seg_other')).toBe('seg_a');
  });

  it('falls back to the first segment when nothing is picked', () => {
    expect(effectiveSegmentId(segments, null)).toBe('seg_a');
  });

  it('is null for a step with no segments', () => {
    expect(effectiveSegmentId([], null)).toBeNull();
    expect(effectiveSegmentId([], 'seg_a')).toBeNull();
  });
});

describe('stepSegments', () => {
  const steps = [
    { key: 'nd_build:1', segments: ['seg_a', 'seg_b'] },
    { key: 'nd_review:1', segments: ['seg_c'] },
  ];

  it("returns the selected step's own segments", () => {
    expect(stepSegments(steps, SELECTION, 'nd_build:1')).toEqual(['seg_a', 'seg_b']);
  });

  it('is empty when the selection key did not parse', () => {
    expect(stepSegments(steps, null, 'nd_build:1')).toEqual([]);
  });

  it('is empty when the key names no step', () => {
    expect(stepSegments(steps, SELECTION, 'nd_deploy:3')).toEqual([]);
  });
});

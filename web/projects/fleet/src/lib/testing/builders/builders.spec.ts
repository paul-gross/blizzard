import {
  addFindingOp,
  assetArtifactView,
  bounceArtifactView,
  bouncedChunkDetail,
  bounceView,
  chunkCountsView,
  chunkDetail,
  chunkEscalationView,
  chunkNeighborhoodView,
  chunkNeighborView,
  chunkUsageTotalView,
  chunkUsageView,
  commitArtifactView,
  decisionView,
  deltaArtifactView,
  escalatedChunkDetail,
  findingCandidate,
  findingDelta,
  findingSurvey,
  FIXTURE_NOW,
  fleetSpendView,
  goneFindingOp,
  landedChunkDetail,
  landedRepoView,
  midFlightChunkDetail,
  observedFindingOp,
  prView,
  questionView,
  queuedChunkDetail,
  routeView,
  sidechainSegmentView,
  stoppedChunkDetail,
  surveyArtifactView,
  toolCallSegmentView,
  transcriptSegmentContentView,
  transcriptSegmentIndexEntry,
  transitionView,
  turnSegmentView,
  workItemEntry,
  workRefView,
} from '../public-api';

/** Milliseconds from {@link FIXTURE_NOW} to `iso`. */
function offsetMs(iso: string | null | undefined): number {
  return Date.parse(iso as string) - FIXTURE_NOW;
}

const BUILDERS: Record<string, () => unknown> = {
  chunkDetail,
  workRefView,
  transitionView,
  chunkEscalationView,
  chunkNeighborView,
  routeView,
  prView,
  landedRepoView,
  chunkUsageTotalView,
  questionView,
  decisionView,
  workItemEntry,
  bounceView,
  chunkUsageView,
  chunkNeighborhoodView,
  queuedChunkDetail,
  midFlightChunkDetail,
  bouncedChunkDetail,
  escalatedChunkDetail,
  landedChunkDetail,
  stoppedChunkDetail,
  assetArtifactView,
  commitArtifactView,
  findingSurvey,
  findingCandidate,
  findingDelta,
  addFindingOp,
  observedFindingOp,
  goneFindingOp,
  surveyArtifactView,
  deltaArtifactView,
  bounceArtifactView,
  turnSegmentView,
  toolCallSegmentView,
  sidechainSegmentView,
  transcriptSegmentIndexEntry,
  transcriptSegmentContentView,
  chunkCountsView,
  fleetSpendView,
};

describe('wire-shape builders', () => {
  for (const [name, build] of Object.entries(BUILDERS)) {
    it(`${name} returns equal records on two calls`, () => {
      const first = build();
      expect(first).toBeTruthy();
      expect(build()).toEqual(first);
    });
  }

  it('takes overrides over its defaults', () => {
    expect(chunkDetail({ status: 'needs_human' }).status).toBe('needs_human');
    expect(assetArtifactView({ key: 'other' }).key).toBe('other');
  });

  it('shapes each named chunk state by its lifecycle', () => {
    expect(queuedChunkDetail()).toMatchObject({ status: 'ready', history: [] });
    expect(midFlightChunkDetail().latest_epoch).toBe(3);
    expect(bouncedChunkDetail().bounces).toHaveLength(1);
    expect(escalatedChunkDetail().escalation).toBeTruthy();
    expect(landedChunkDetail()).toMatchObject({ status: 'done', landed: true });
    expect(stoppedChunkDetail().status).toBe('stopped');
  });

  it('dates every instant as an offset from FIXTURE_NOW', () => {
    expect(offsetMs(transitionView().recorded_at)).toBe(-60_000);
    expect(offsetMs(bounceView().recorded_at)).toBe(-45_000);
    expect(offsetMs(assetArtifactView().recorded_at)).toBe(-30_000);
    expect(offsetMs(commitArtifactView().recorded_at)).toBe(-30_000);
    expect(offsetMs(questionView().asked_at)).toBe(-60_000);
    expect(offsetMs(decisionView().submitted_at)).toBe(-60_000);
    expect(offsetMs(workItemEntry().fetched_at)).toBe(0);
    expect(offsetMs(turnSegmentView().timestamp)).toBe(-120_000);
    expect(offsetMs(transcriptSegmentIndexEntry().received_at)).toBe(-60_000);
    expect(offsetMs(fleetSpendView().since)).toBe(-24 * 3_600_000);
    expect(offsetMs(fleetSpendView().until)).toBe(0);
    expect(offsetMs(new Date(FIXTURE_NOW).toISOString())).toBe(0);
  });

  it('serializes the content shapes into the assets that carry them', () => {
    const survey = findingSurvey({ scope: 'api' });
    const delta = findingDelta({
      findings: [addFindingOp(), observedFindingOp(), goneFindingOp()],
    });
    expect(JSON.parse(surveyArtifactView(survey).content as string)).toEqual(survey);
    expect(JSON.parse(deltaArtifactView(delta).content as string)).toEqual(delta);
    expect(bounceArtifactView().key).toBe('bounce-envelope');
  });
});

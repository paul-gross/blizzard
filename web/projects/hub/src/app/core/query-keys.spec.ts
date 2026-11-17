import { describe, expect, it } from 'vitest';

import { chunkDetailKey, chunkTranscriptSegmentKey, chunkTranscriptsKey, chunkWorkItemsKey } from 'fleet';
import {
  hubActivityKey,
  hubAuthProvidersKey,
  hubBacklogKey,
  hubBoardChunksKey,
  hubChunkCountsKey,
  hubChunksKey,
  hubConfigChangesKey,
  hubConfigKey,
  hubDecisionsKey,
  hubEnvTimelineKey,
  hubEnvTimelineLiveKey,
  hubEventsKey,
  hubFindingPrefixKey,
  hubFindingsBucketPrefixKey,
  hubFlagsKey,
  hubFleetSpendKey,
  hubFlowsKey,
  hubGardenProposalsKey,
  hubGraphsKey,
  hubHealthKey,
  hubHostsKey,
  hubMeKey,
  hubMembersKey,
  hubProjectsKey,
  hubQuestionsKey,
  hubQueueKey,
  hubRoutineProposalCountsPrefixKey,
  hubRoutinesKey,
  hubRunnerNextClaimKey,
  hubRunnersKey,
  hubScopesKey,
  hubUrgenciesKey,
  hubBoardChunkWorkItemsKey,
  hubChunkKey,
  hubChunkWorkItemsKey,
  hubConfigHistoryKey,
  hubConfigListKey,
  hubConfigRecordKey,
  hubFindingClassesKey,
  hubFindingKey,
  hubFindingsBucketKey,
  hubFindingsKey,
  hubGardenProposalKey,
  hubGraphKey,
  hubProjectKey,
  hubProjectLinksKey,
  hubRoutineBaselinesKey,
  hubRoutineProposalCountsKey,
  hubRoutineScopesKey,
  hubRoutineSweepsKey,
  hubRoutineTrendKey,
  hubRunDeltaKey,
  hubRunsKey,
  hubScopeRoutinesKey,
  hubUrgencyIconKey,
  hubWorkItemKey,
  hubWorkItemsBatchKey,
  hubWorkItemsKey,
  lensedKey,
} from './query-keys';

/** Pins the literal array every query key returns, so a key's identity — what an SSE registry prefix reaches and what
 * a cache entry is shared under — is the same wherever the key is defined. */
describe('query key identity', () => {
  it.each([
  ['hubActivityKey', hubActivityKey, ['hub', 'activity']],
  ['hubAuthProvidersKey', hubAuthProvidersKey, ['hub', 'auth', 'providers']],
  ['hubBacklogKey', hubBacklogKey, ['hub', 'backlog']],
  ['hubBoardChunksKey', hubBoardChunksKey, ['hub', 'chunks', 'board']],
  ['hubChunkCountsKey', hubChunkCountsKey, ['hub', 'chunks', 'counts']],
  ['hubChunksKey', hubChunksKey, ['hub', 'chunks']],
  ['hubConfigChangesKey', hubConfigChangesKey, ['hub', 'config', 'changes']],
  ['hubConfigKey', hubConfigKey, ['hub', 'config']],
  ['hubDecisionsKey', hubDecisionsKey, ['hub', 'decisions']],
  ['hubEnvTimelineKey', hubEnvTimelineKey, ['hub', 'env-timeline']],
  ['hubEnvTimelineLiveKey', hubEnvTimelineLiveKey, ['hub', 'env-timeline', 'live']],
  ['hubEventsKey', hubEventsKey, ['hub', 'events']],
  ['hubFindingPrefixKey', hubFindingPrefixKey, ['hub', 'finding']],
  ['hubFindingsBucketPrefixKey', hubFindingsBucketPrefixKey, ['hub', 'findings-bucket']],
  ['hubFlagsKey', hubFlagsKey, ['hub', 'flags']],
  ['hubFleetSpendKey', hubFleetSpendKey, ['hub', 'fleet-spend']],
  ['hubFlowsKey', hubFlowsKey, ['hub', 'flows']],
  ['hubGardenProposalsKey', hubGardenProposalsKey, ['hub', 'garden-proposals']],
  ['hubGraphsKey', hubGraphsKey, ['hub', 'graphs']],
  ['hubHealthKey', hubHealthKey, ['hub', 'health']],
  ['hubHostsKey', hubHostsKey, ['hub', 'hosts']],
  ['hubMeKey', hubMeKey, ['hub', 'me']],
  ['hubMembersKey', hubMembersKey, ['hub', 'members']],
  ['hubProjectsKey', hubProjectsKey, ['hub', 'config', 'projects']],
  ['hubQuestionsKey', hubQuestionsKey, ['hub', 'questions']],
  ['hubQueueKey', hubQueueKey, ['hub', 'queue']],
  ['hubRoutineProposalCountsPrefixKey', hubRoutineProposalCountsPrefixKey, ['hub', 'routine-proposal-counts']],
  ['hubRoutinesKey', hubRoutinesKey, ['hub', 'routines']],
  ['hubRunnerNextClaimKey', hubRunnerNextClaimKey, ['hub', 'runner-next-claim']],
  ['hubRunnersKey', hubRunnersKey, ['hub', 'runners']],
  ['hubScopesKey', hubScopesKey, ['hub', 'scopes']],
  ['hubUrgenciesKey', hubUrgenciesKey, ['hub', 'config', 'urgencies']],
  ])('%s is a fixed literal', (_name, actual, expected) => {
    expect(actual).toEqual(expected);
  });

  it('the parameterised hub keys return their literal shape', () => {
    expect(hubConfigListKey('forges', true)).toEqual(['hub', 'config', 'forges', 'list', true]);
    expect(hubConfigRecordKey('forges', 'f1')).toEqual(['hub', 'config', 'forges', 'record', 'f1']);
    expect(hubConfigHistoryKey('forge', 'f1')).toEqual(['hub', 'config', 'history', 'forge', 'f1']);
    expect(hubUrgencyIconKey('t', 'k', 'd')).toEqual(['hub', 'urgency-icon', 't', 'k', 'd']);
    expect(hubProjectKey('p1')).toEqual(['hub', 'config', 'projects', 'p1']);
    expect(hubProjectLinksKey('p1', 'source')).toEqual(['hub', 'config', 'projects', 'p1', 'links', 'source']);
    expect(hubGardenProposalKey('gp1')).toEqual(['hub', 'garden-proposal', 'gp1']);
    expect(hubFindingsKey(['f1', 'f2'])).toEqual(['hub', 'findings', 'f1', 'f2']);
    expect(hubFindingKey('f1')).toEqual(['hub', 'finding', 'f1']);
    expect(hubFindingsBucketKey('r', 's', 'c', 'live')).toEqual(['hub', 'findings-bucket', 'pages', 'r', 's', 'c', 'live']);
    expect(hubFindingsBucketKey(null, null)).toEqual(['hub', 'findings-bucket', 'pages', null, null, null, null]);
    expect(hubFindingClassesKey('r', 's')).toEqual(['hub', 'findings-bucket', 'classes', 'r', 's']);
    expect(hubWorkItemKey('src', 'ref')).toEqual(['hub', 'work-item', 'src', 'ref']);
    expect(hubWorkItemsKey([['a', '1'], ['b', '2']])).toEqual(['hub', 'work-items', 'a', '1', 'b', '2']);
    expect(hubRoutineTrendKey('r', 's', 'u', 'b', 7)).toEqual(['hub', 'routine-trend', 'r', 's', 'u', 'b', 7]);
    expect(hubRoutineSweepsKey('r1', 's', 'u')).toEqual(['hub', 'routine-sweeps', 'r1', 's', 'u']);
    expect(hubRoutineProposalCountsKey('r', 's', 'u')).toEqual(['hub', 'routine-proposal-counts', 'r', 's', 'u']);
    expect(hubRoutineBaselinesKey('r1')).toEqual(['hub', 'routines', 'r1', 'baselines']);
    expect(hubRoutineScopesKey('r1')).toEqual(['hub', 'routine', 'r1', 'scopes']);
    expect(hubScopeRoutinesKey('sc')).toEqual(['hub', 'scope', 'sc', 'routines']);
    expect(hubRunsKey('s')).toEqual(['hub', 'runs', 's']);
    expect(hubRunDeltaKey('ch_1')).toEqual(['hub', 'run', 'ch_1']);
    expect(hubChunkKey('ch_1')).toEqual(['hub', 'chunk', 'ch_1']);
    expect(hubChunkWorkItemsKey('ch_1')).toEqual(['hub', 'chunk', 'ch_1', 'work-items']);
    expect(hubWorkItemsBatchKey(['a', 'b'])).toEqual(['hub', 'work-items-batch', ['a', 'b']]);
    expect(hubBoardChunkWorkItemsKey('ch_1')).toEqual(['hub', 'board-work-items', 'ch_1']);
    expect(hubGraphKey('g1')).toEqual(['hub', 'graph', 'g1']);
  });

  it('lensedKey appends the project part to the key', () => {
    expect(lensedKey(['hub', 'chunks'], 'p1')).toEqual(['hub', 'chunks', { project: 'p1' }]);
    expect(lensedKey(['hub', 'chunks'], null)).toEqual(['hub', 'chunks', { project: null }]);
  });

  it.each(['hub', 'rn_1'])('the plane-root chunk helpers lead with the root %s', (root) => {
    expect(chunkTranscriptsKey(root, 'ch_1')).toEqual([root, 'chunk', 'ch_1', 'transcripts']);
    expect(chunkTranscriptSegmentKey(root, 'ch_1', 'sg_1', true)).toEqual([root, 'chunk-transcript-segment', 'ch_1', 'sg_1']);
    expect(chunkTranscriptSegmentKey(root, 'ch_1', 'sg_1', false)).toEqual([root, 'chunk', 'ch_1', 'transcript-segment', 'sg_1']);
  });

  it('the chunk detail and work-items helpers write the hub shape for the hub root and the runner shape otherwise', () => {
    expect(chunkDetailKey('hub', 'ch_1')).toEqual(['hub', 'chunk', 'ch_1']);
    expect(chunkWorkItemsKey('hub', 'ch_1')).toEqual(['hub', 'chunk', 'ch_1', 'work-items']);
    expect(chunkDetailKey('rn_1', 'ch_1')).toEqual(['rn_1', 'chunk', 'ch_1', 'detail']);
    expect(chunkWorkItemsKey('rn_1', 'ch_1')).toEqual(['rn_1', 'chunk', 'ch_1', 'work-items-detail']);
  });
});

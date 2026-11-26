import { describe, expect, it } from 'vitest';
import type { hubApi } from 'fleet/hub-api';
import { toNextClaim, toPrerequisiteStatus } from './runner-next-claim.translate';

const entry: hubApi.NextClaimEntry = { chunk_id: 'ch_next', graph_id: 'gr_1', position: 3, project_id: 'p', project_name: 'Example', project_slug: 'example', urgency: { urgency_id: 'u', slug: 'urgent' } };
const blocked: hubApi.NextClaimPassedEntry = { ...entry, chunk_id: 'ch_passed', reason: 'blocked', prerequisite: 'ch_prereq', prerequisite_count: 3 };
const response: hubApi.NextClaimResponse = { next: entry, passed: [blocked], passed_total: 22, held_at: [blocked], if_holding: null, unserved: 2, without_repository_targets: null };

describe('next claim translation', () => {
  it('carries the hub walk and resolves each urgency reference', () => {
    const next = toNextClaim(response);
    expect(next.next).toEqual({ chunk_id: 'ch_next', graph_id: 'gr_1', position: 3, project_id: 'p', project_name: 'Example', project_slug: 'example', urgency: { id: 'u', slug: 'urgent' } });
    expect(next.passed[0]).toMatchObject({ chunk_id: 'ch_passed', reason: 'blocked', prerequisite: 'ch_prereq', prerequisite_count: 3 });
    expect(next.passed_total).toBe(22);
    expect(next.unserved).toBe(2);
  });

  it('keeps both known pass reasons and maps an unforeseen one onto unknown', () => {
    const next = toNextClaim({ ...response, passed: [blocked, { ...blocked, reason: 'ineligible' }, { ...blocked, reason: 'quarantined' as never }] });
    expect(next.passed.map((passed) => passed.reason)).toEqual(['blocked', 'ineligible', { kind: 'unknown', raw: 'quarantined' }]);
  });

  it('carries the no-target alternative, and reads an older hub that omits it as not reported', () => {
    const alternative: hubApi.NextClaimScenario = { next: null, passed: [], passed_total: 0, unserved: 0, if_holding: null, held_at: [blocked], excluded: 2 };
    expect(toNextClaim({ ...response, without_repository_targets: alternative }).without_repository_targets).toMatchObject({ excluded: 2, next: null });
    const olderHub = Object.fromEntries(Object.entries(response).filter(([key]) => key !== 'without_repository_targets'));
    expect(toNextClaim(olderHub as hubApi.NextClaimResponse).without_repository_targets).toBeNull();
  });

  it('projects a prerequisite to its id and lane status, an unforeseen status to unknown', () => {
    expect(toPrerequisiteStatus({ chunk_id: 'ch_a', status: 'running' } as hubApi.ChunkDetail)).toEqual({ chunk_id: 'ch_a', laneStatus: { kind: 'running' } });
    expect(toPrerequisiteStatus({ chunk_id: 'ch_a', status: 'orbiting' } as unknown as hubApi.ChunkDetail).laneStatus).toEqual({ kind: 'unknown', raw: 'orbiting' });
  });
});

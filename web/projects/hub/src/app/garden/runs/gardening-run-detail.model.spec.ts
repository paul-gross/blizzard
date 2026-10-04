import { type RunDeltaView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { runDeltaVm } from './gardening-run-detail.model';

type DeliveredSet = RunDeltaView['sets'][number];

const delta = (fields: Partial<RunDeltaView> = {}): RunDeltaView =>
  ({
    chunk_id: 'ch_1',
    escalation: null,
    mode: 'sweep',
    outcome: 'done',
    routine_name: 'docs',
    scope_slug: 'web',
    sets: [],
    ...fields,
  }) as RunDeltaView;

const set = {
  finding_set_id: 'fs_1',
  revisions: { zeta: 'b2', alpha: 'a1' },
  measurement: null,
  added: [{ finding_id: 'f1', class: 'drift', locus: 'a.ts', summary: 'new', introduced: true }],
  observed: [{ finding_id: 'f2', class: 'drift', locus: 'b.ts', summary: 'still' }],
  gone: [{ finding_id: 'f3', note: 'fixed' }],
} as unknown as DeliveredSet;

const noMintedAt = (): string | null => null;

describe('runDeltaVm', () => {
  it('is null until the delta read resolves', () => {
    expect(runDeltaVm(undefined, noMintedAt)).toBeNull();
  });

  it('takes minted_at from the supplied lookup, keyed by the run', () => {
    const vm = runDeltaVm(delta(), (chunkId) => (chunkId === 'ch_1' ? '2026-06-01T00:00:00Z' : null));
    expect(vm).toMatchObject({ chunkId: 'ch_1', routineName: 'docs', scopeSlug: 'web', mintedAt: '2026-06-01T00:00:00Z' });
  });

  it('maps no escalation to null and a present one field by field', () => {
    expect(runDeltaVm(delta(), noMintedAt)?.escalation).toBeNull();
    const escalation = { node_name: 'build', takeover_command: 'tk', wrapped_takeover_command: 'wtk' };
    expect(runDeltaVm(delta({ escalation }), noMintedAt)?.escalation).toEqual({
      nodeName: 'build',
      takeoverCommand: 'tk',
      wrappedTakeoverCommand: 'wtk',
    });
  });

  it('shapes each set, labelling revisions sorted by repo', () => {
    expect(runDeltaVm(delta({ sets: [set] }), noMintedAt)?.sets).toEqual([
      {
        findingSetId: 'fs_1',
        revisionsLabel: 'alpha@a1, zeta@b2',
        measurement: null,
        added: [{ findingId: 'f1', findingClass: 'drift', locus: 'a.ts', summary: 'new', introduced: true }],
        observed: [{ findingId: 'f2', findingClass: 'drift', locus: 'b.ts', summary: 'still' }],
        gone: [{ findingId: 'f3', note: 'fixed' }],
      },
    ]);
  });

  it('labels a set with no revisions as a dash', () => {
    const bare = { ...set, revisions: {} } as DeliveredSet;
    expect(runDeltaVm(delta({ sets: [bare] }), noMintedAt)?.sets[0].revisionsLabel).toBe('—');
  });
});

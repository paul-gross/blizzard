import { gardenRoutine } from '../core/routine.builders';
import { gardenRunDelta, gardenRunDeltaSet, gardenRunEscalation } from '../core/run.builders';
import type { RunDelta } from '../core/run.domain';
import { describe, expect, it } from 'vitest';

import { runDeltaVm } from './gardening-run-detail.model';

const delta = (fields: Partial<RunDelta> = {}): RunDelta =>
  gardenRunDelta({ chunkId: 'ch_1', routineSlug: 'docs', scopeSlug: 'web', ...fields });

const set = gardenRunDeltaSet({
  findingSetId: 'fs_1',
  revisions: { zeta: 'b2', alpha: 'a1' },
  added: [{ findingId: 'f1', findingClass: 'drift', locus: 'a.ts', summary: 'new', introduced: '2026-01-01T00:00:00Z' }],
  observed: [{ findingId: 'f2', findingClass: 'drift', locus: 'b.ts', summary: 'still' }],
  gone: [{ findingId: 'f3', note: 'fixed' }],
});

const noMintedAt = (): string | null => null;

describe('runDeltaVm', () => {
  it('is null until the delta read resolves', () => {
    expect(runDeltaVm(undefined, noMintedAt, [])).toBeNull();
  });

  it("names the run's routine by its display name, looked up by the slug the delta carries", () => {
    expect(runDeltaVm(delta(), noMintedAt, [gardenRoutine({ slug: 'docs', name: 'Docs sweep' })])?.routineName).toBe(
      'Docs sweep',
    );
  });

  it('takes minted_at from the supplied lookup, keyed by the run', () => {
    const vm = runDeltaVm(delta(), (chunkId) => (chunkId === 'ch_1' ? '2026-06-01T00:00:00Z' : null), []);
    expect(vm).toMatchObject({ chunkId: 'ch_1', routineName: 'docs', scopeSlug: 'web', mintedAt: '2026-06-01T00:00:00Z' });
  });

  it('maps no escalation to null and a present one field by field', () => {
    expect(runDeltaVm(delta(), noMintedAt, [])?.escalation).toBeNull();
    const escalation = gardenRunEscalation({ nodeName: 'build', takeoverCommand: 'tk', wrappedTakeoverCommand: 'wtk' });
    expect(runDeltaVm(delta({ escalation }), noMintedAt, [])?.escalation).toEqual({
      nodeName: 'build',
      takeoverCommand: 'tk',
      wrappedTakeoverCommand: 'wtk',
    });
  });

  it('names no cadence for a run started by hand', () => {
    expect(runDeltaVm(delta(), noMintedAt, [])?.cadence).toBeNull();
  });

  it('names the cadence and chore of a run a cadence minted', () => {
    expect(runDeltaVm(delta({ cadence: { slug: 'evening', name: 'Evening', choreKey: 'nightly' } }), noMintedAt, [])?.cadence).toEqual({
      slug: 'evening',
      name: 'Evening',
      choreKey: 'nightly',
    });
  });

  it('keeps the cadence but no chore key once the chore was removed', () => {
    expect(runDeltaVm(delta({ cadence: { slug: 'evening', name: 'evening', choreKey: null } }), noMintedAt, [])?.cadence).toEqual({
      slug: 'evening',
      name: 'evening',
      choreKey: null,
    });
  });

  it('shapes each set, labelling revisions sorted by repo', () => {
    expect(runDeltaVm(delta({ sets: [set] }), noMintedAt, [])?.sets).toEqual([
      {
        findingSetId: 'fs_1',
        revisionsLabel: 'alpha@a1, zeta@b2',
        measurement: null,
        added: [{ findingId: 'f1', findingClass: 'drift', locus: 'a.ts', summary: 'new', introduced: '2026-01-01T00:00:00Z' }],
        observed: [{ findingId: 'f2', findingClass: 'drift', locus: 'b.ts', summary: 'still' }],
        gone: [{ findingId: 'f3', note: 'fixed' }],
      },
    ]);
  });

  it('labels a set with no revisions as a dash', () => {
    const bare = { ...set, revisions: {} };
    expect(runDeltaVm(delta({ sets: [bare] }), noMintedAt, [])?.sets[0].revisionsLabel).toBe('—');
  });
});

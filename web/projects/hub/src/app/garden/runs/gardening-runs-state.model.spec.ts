import { gardenRoutine } from '../core/routine.builders';
import { gardenDeliveredSet, gardenRunEscalation, gardenRunRow } from '../core/run.builders';
import { describe, expect, it } from 'vitest';

import { mintedAtFor, runListRows, summedCounts } from './gardening-runs-state.model';

const DELIVERED_RUN = gardenRunRow({
  chunkId: 'ch_1',
  routineName: 'nightly',
  scopeSlug: 'blizzard',
  mode: 'full',
  mintedAt: '2026-01-10T00:00:00Z',
  laneStatus: { kind: 'done' },
  delivered: [
    gardenDeliveredSet({ addedCount: 1, observedCount: 11, goneCount: 0 }),
    gardenDeliveredSet({ addedCount: 0, observedCount: 0, goneCount: 2 }),
  ],
});

const ESCALATED_RUN = gardenRunRow({
  chunkId: 'ch_2',
  routineName: 'nightly',
  scopeSlug: 'web',
  mode: 'delta',
  mintedAt: '2026-01-11T00:00:00Z',
  laneStatus: { kind: 'needs_human' },
  escalation: gardenRunEscalation({ takeoverCommand: 'blizzard hub chunk takeover ch_2' }),
});

describe('summedCounts', () => {
  it('answers null for a run that delivered no set', () => {
    expect(summedCounts([])).toBeNull();
  });

  it('sums each leg across every delivered set', () => {
    expect(summedCounts(DELIVERED_RUN.delivered)).toEqual({ added: 1, observed: 11, gone: 2 });
  });
});

describe('runListRows', () => {
  it('maps each run in order, reading escalated off a non-null escalation', () => {
    expect(runListRows([DELIVERED_RUN, ESCALATED_RUN], [])).toEqual([
      {
        chunkId: 'ch_1',
        routineName: 'nightly',
        scopeSlug: 'blizzard',
        mode: 'full',
        mintedAt: '2026-01-10T00:00:00Z',
        laneStatus: { kind: 'done' },
        escalated: false,
        counts: { added: 1, observed: 11, gone: 2 },
      },
      {
        chunkId: 'ch_2',
        routineName: 'nightly',
        scopeSlug: 'web',
        mode: 'delta',
        mintedAt: '2026-01-11T00:00:00Z',
        laneStatus: { kind: 'needs_human' },
        escalated: true,
        counts: null,
      },
    ]);
  });

  it('renders a mode this build does not know raw', () => {
    expect(runListRows([gardenRunRow({ chunkId: 'ch_3', mode: { unlisted: 'sampled' } })], [])[0].mode).toBe('sampled');
  });

  it("names each run's routine by its display name, looked up by the slug the run carries", () => {
    const rows = runListRows([DELIVERED_RUN], [gardenRoutine({ slug: 'nightly', name: 'Nightly sweep' })]);
    expect(rows[0].routineName).toBe('Nightly sweep');
  });
});

describe('mintedAtFor', () => {
  const rows = runListRows([DELIVERED_RUN, ESCALATED_RUN], []);

  it('answers the minted instant off the matching row', () => {
    expect(mintedAtFor(rows, 'ch_2')).toBe('2026-01-11T00:00:00Z');
  });

  it('answers null for a chunk no row carries', () => {
    expect(mintedAtFor(rows, 'ch_gone')).toBeNull();
  });
});

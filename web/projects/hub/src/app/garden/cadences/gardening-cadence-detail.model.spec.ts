import { formatAbsolute } from 'fleet';
import { describe, expect, it } from 'vitest';

import {
  gardenCadence,
  gardenCadenceAttention,
  gardenCadenceErrand,
  gardenChore,
  gardenErrandRun,
} from '../core/cadence.builders';
import {
  cadenceBySlug,
  cadenceOverrideRetired,
  cadencePanelVm,
  choreScopesText,
  errandRow,
  whileEditing,
} from './gardening-cadence-detail.model';

const NIGHTLY = gardenChore();
/** A chore whose mode this build does not know — it renders raw. */
const ODD = gardenChore({
  id: 'chr_2',
  key: 'odd',
  routine: 'docs',
  scopes: ['web', 'hub'],
  mode: { unlisted: 'sampled' },
  promote: false,
  note: 'keep',
});

describe('cadenceBySlug', () => {
  it('resolves a slug against the list', () => {
    const cadences = [gardenCadence({ slug: 'evening' })];
    expect(cadenceBySlug('evening', cadences)?.slug).toBe('evening');
    expect(cadenceBySlug('gone', cadences)).toBeNull();
    expect(cadenceBySlug(null, cadences)).toBeNull();
  });
});

describe('cadenceOverrideRetired', () => {
  it('reads the pending direction for this cadence only', () => {
    const cadence = gardenCadence({ slug: 'evening' });
    expect(cadenceOverrideRetired(cadence, [{ cadenceId: cadence.id, revision: 1, retired: true }])).toBe(true);
    expect(cadenceOverrideRetired(cadence, [{ cadenceId: 'cad_other', revision: 1, retired: true }])).toBeNull();
    expect(cadenceOverrideRetired(null, [])).toBeNull();
  });
});

describe('choreScopesText', () => {
  it('reads every scope, a list, or the default', () => {
    expect(choreScopesText('all')).toBe('all scopes');
    expect(choreScopesText(['web', 'hub'])).toBe('web, hub');
    expect(choreScopesText(null)).toBe("the routine's default scope");
  });
});

describe('errandRow', () => {
  it('carries the scope, state, last run, due, and why it is stopped', () => {
    const run = gardenErrandRun();
    expect(
      errandRow(
        gardenCadenceErrand({
          scope: 'gone',
          state: 'stopped',
          dueAt: null,
          lastRun: run,
          attention: [{ chore: 'nightly', scope: 'gone', reason: 'scope_unknown' }],
        }),
      ),
    ).toEqual({
      scope: 'gone',
      label: 'stopped',
      tone: 'stale',
      lastRunChunkId: run.chunkId,
      lastRunAt: run.scheduledAt,
      dueAt: null,
      attention: 'the scope does not exist',
    });
  });

  it('renders a state this build does not know raw, in the idle tone', () => {
    const row = errandRow(gardenCadenceErrand({ state: { unlisted: 'parked' } }));
    expect(row.label).toBe('parked');
    expect(row.tone).toBe('idle');
  });
});

describe('cadencePanelVm', () => {
  const cadence = gardenCadence({
    slug: 'evening',
    chores: [NIGHTLY, ODD],
    attention: [gardenCadenceAttention(), gardenCadenceAttention({ chore: null, scope: null, reason: 'no_chores' })],
  });

  it('lists all five moments as full local text, earliest first', () => {
    const vm = cadencePanelVm(cadence, null, undefined)!;
    expect(vm.nextMoments).toHaveLength(5);
    expect(vm.nextMoments.map((m) => m.iso)).toEqual(cadence.nextMoments);
    expect(vm.nextMoments[0].text).toBe(formatAbsolute(cadence.nextMoments[0]));
  });

  it('lists what needs attention, an unknown reason raw', () => {
    const vm = cadencePanelVm(
      { ...cadence, attention: [...cadence.attention, gardenCadenceAttention({ reason: { unlisted: 'mystery' } })] },
      null,
      undefined,
    )!;
    expect(vm.attention).toEqual([
      { chore: 'nightly', scope: 'gone', reason: 'the scope does not exist' },
      { chore: null, scope: null, reason: 'the cadence keeps no chore' },
      { chore: 'nightly', scope: 'gone', reason: 'mystery' },
    ]);
  });

  it('holds each chore’s errands loading until they are read, then that chore’s own', () => {
    expect(cadencePanelVm(cadence, null, undefined)!.chores.map((c) => c.errandsState)).toEqual(['loading', 'loading']);
    const vm = cadencePanelVm(cadence, null, [
      gardenCadenceErrand({ chore: 'nightly', scope: 'a' }),
      gardenCadenceErrand({ chore: 'nightly', scope: 'b' }),
      gardenCadenceErrand({ chore: 'odd', scope: 'web' }),
    ])!;
    expect(vm.chores[0].errands?.map((e) => e.scope)).toEqual(['a', 'b']);
    expect(vm.chores[1].errands?.map((e) => e.scope)).toEqual(['web']);
    expect(vm.chores[1].mode).toBe('sampled');
  });

  it('derives each chore state from the query and its filtered errands, including a background refetch', () => {
    const errands = [gardenCadenceErrand({ chore: 'nightly', scope: 'a' })];
    const query = (pending: boolean, error: boolean) => ({ isPending: () => pending, isError: () => error });
    expect(cadencePanelVm(cadence, null, undefined, query(true, false))!.chores.map((c) => c.errandsState)).toEqual(['loading', 'loading']);
    expect(cadencePanelVm(cadence, null, undefined, query(false, true))!.chores.map((c) => c.errandsState)).toEqual(['error', 'error']);
    const refetch = { ...query(false, false), isFetching: () => true };
    expect(cadencePanelVm(cadence, null, errands, refetch)!.chores.map((c) => c.errandsState)).toEqual(['ready', 'empty']);
    expect(cadencePanelVm(cadence, null, [], query(false, false))!.chores.map((c) => c.errandsState)).toEqual(['empty', 'empty']);
  });

  it('renders the pending lifecycle, keeping the real flag', () => {
    const vm = cadencePanelVm(cadence, true, undefined)!;
    expect(vm.retired).toBe(false);
    expect(vm.renderedRetired).toBe(true);
  });

  it('is null with no cadence', () => {
    expect(cadencePanelVm(null, null, undefined)).toBeNull();
  });
});

describe('whileEditing', () => {
  it('hands out the tree only while a baseline is held', () => {
    const tree = { field: 'tree' };
    expect(whileEditing(null, tree)).toBeNull();
    expect(whileEditing({ id: 'cad_evening', revision: 1 }, tree)).toBe(tree);
  });
});

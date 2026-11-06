import { type ChoreView, formatAbsolute } from 'fleet';
import {
  gardenCadenceAttentionView,
  gardenCadenceErrandView,
  gardenCadenceView,
  gardenChoreView,
  gardenErrandRunView,
} from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import {
  cadenceBySlug,
  cadenceDraftOf,
  cadenceOverrideRetired,
  cadencePanelVm,
  choreDraftOf,
  choreScopesText,
  choresAdding,
  choresRemoving,
  choresReplacing,
  draftDocument,
  editCadenceRequest,
  errandRow,
  spacingInput,
} from './gardening-cadence-detail.model';

const NIGHTLY = gardenChoreView();
/** A chore whose mode this build does not know — it must travel back verbatim. */
const ODD = gardenChoreView({
  chore_id: 'chr_2',
  key: 'odd',
  routine: 'docs',
  scopes: ['web', 'hub'],
  mode: 'sampled' as ChoreView['mode'],
  promote: false,
  note: 'keep',
});

describe('cadenceBySlug', () => {
  it('resolves a slug against the list', () => {
    const cadences = [gardenCadenceView({ slug: 'evening' })];
    expect(cadenceBySlug('evening', cadences)?.slug).toBe('evening');
    expect(cadenceBySlug('gone', cadences)).toBeNull();
    expect(cadenceBySlug(null, cadences)).toBeNull();
  });
});

describe('cadenceOverrideRetired', () => {
  it('reads the pending direction for this cadence only', () => {
    const cadence = gardenCadenceView({ slug: 'evening' });
    expect(cadenceOverrideRetired(cadence, [{ cadenceId: cadence.cadence_id, revision: 1, retired: true }])).toBe(true);
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
    const run = gardenErrandRunView();
    expect(
      errandRow(
        gardenCadenceErrandView({
          scope: 'gone',
          state: 'stopped',
          due_at: null,
          last_run: run,
          attention: [gardenCadenceAttentionView()],
        }),
      ),
    ).toEqual({
      scope: 'gone',
      label: 'stopped',
      tone: 'stale',
      lastRunChunkId: run.chunk_id,
      lastRunAt: run.scheduled_at,
      dueAt: null,
      attention: 'the scope does not exist',
    });
  });

  it('renders a state this build does not know raw, in the idle tone', () => {
    const row = errandRow(gardenCadenceErrandView({ state: 'parked' as never }));
    expect(row.label).toBe('parked');
    expect(row.tone).toBe('idle');
  });
});

describe('cadencePanelVm', () => {
  const cadence = gardenCadenceView({
    slug: 'evening',
    chores: [NIGHTLY, ODD],
    attention: [gardenCadenceAttentionView(), gardenCadenceAttentionView({ chore: null, scope: null, reason: 'no_chores' })],
  });

  it('lists all five moments as full local text, earliest first', () => {
    const vm = cadencePanelVm(cadence, null, undefined)!;
    expect(vm.nextMoments).toHaveLength(5);
    expect(vm.nextMoments.map((m) => m.iso)).toEqual(cadence.next_moments);
    expect(vm.nextMoments[0].text).toBe(formatAbsolute(cadence.next_moments[0]));
  });

  it('lists what needs attention, an unknown reason raw', () => {
    const vm = cadencePanelVm(
      { ...cadence, attention: [...cadence.attention, gardenCadenceAttentionView({ reason: 'mystery' as never })] },
      null,
      undefined,
    )!;
    expect(vm.attention).toEqual([
      { chore: 'nightly', scope: 'gone', reason: 'the scope does not exist' },
      { chore: null, scope: null, reason: 'the cadence keeps no chore' },
      { chore: 'nightly', scope: 'gone', reason: 'mystery' },
    ]);
  });

  it('holds each chore’s errands null until they are read, then that chore’s own', () => {
    expect(cadencePanelVm(cadence, null, undefined)!.chores.map((c) => c.errands)).toEqual([null, null]);
    const vm = cadencePanelVm(cadence, null, [
      gardenCadenceErrandView({ chore: 'nightly', scope: 'a' }),
      gardenCadenceErrandView({ chore: 'nightly', scope: 'b' }),
      gardenCadenceErrandView({ chore: 'odd', scope: 'web' }),
    ])!;
    expect(vm.chores[0].errands?.map((e) => e.scope)).toEqual(['a', 'b']);
    expect(vm.chores[1].errands?.map((e) => e.scope)).toEqual(['web']);
    expect(vm.chores[1].mode).toBe('sampled');
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

describe('the cadence edit form', () => {
  it('opens on the cadence’s own rhythm, its spacing as the hub reads it', () => {
    expect(cadenceDraftOf(gardenCadenceView({ slug: 'evening', anchor: 'finished' }))).toEqual({
      slug: 'evening',
      name: 'evening',
      cron: '0 20 * * *',
      zone: 'America/Chicago',
      spacing: '10d',
      anchor: 'finished',
    });
    expect(spacingInput(360)).toBe('6h');
    expect(spacingInput(90)).toBe('90m');
    expect(spacingInput(null)).toBe('');
  });

  it('sends the whole rhythm, a blank part clearing it', () => {
    expect(
      editCadenceRequest({ slug: 'evening', name: ' Evening ', cron: '', zone: '', spacing: '', anchor: '' }),
    ).toEqual({ name: 'Evening', schedule: null, spacing: null, anchor: null });
    expect(
      editCadenceRequest({ slug: 'evening', name: 'e', cron: '0 21 * * *', zone: 'UTC', spacing: '2d', anchor: 'started' }),
    ).toEqual({ name: 'e', schedule: { cron: '0 21 * * *', zone: 'UTC' }, spacing: '2d', anchor: 'started' });
  });
});

describe('the whole chore list', () => {
  const served = [NIGHTLY, ODD];
  const nightlyDoc = { key: 'nightly', routine: 'nightly', scopes: 'all', mode: 'full', promote: true, note: null };
  const oddDoc = { key: 'odd', routine: 'docs', scopes: ['web', 'hub'], mode: 'sampled', promote: false, note: 'keep' };

  it('adds a chore at the end, carrying the others verbatim, an unknown mode included', () => {
    expect(
      choresAdding(served, { key: 'weekly', routine: 'deep', scopes: 'a, b c', mode: 'delta', promote: false, note: '' }),
    ).toEqual([nightlyDoc, oddDoc, { key: 'weekly', routine: 'deep', scopes: ['a', 'b', 'c'], mode: 'delta', promote: false, note: null }]);
  });

  it('replaces one chore in place', () => {
    expect(choresReplacing(served, 'nightly', { ...choreDraftOf(NIGHTLY), promote: false })).toEqual([
      { ...nightlyDoc, promote: false },
      oddDoc,
    ]);
  });

  it('removes one chore', () => {
    expect(choresRemoving(served, 'nightly')).toEqual([oddDoc]);
  });

  it('round-trips a served chore through its draft unchanged', () => {
    expect(draftDocument(choreDraftOf(ODD))).toEqual(oddDoc);
    expect(draftDocument(choreDraftOf(gardenChoreView({ scopes: null })))).toEqual({ ...nightlyDoc, scopes: null });
  });
});

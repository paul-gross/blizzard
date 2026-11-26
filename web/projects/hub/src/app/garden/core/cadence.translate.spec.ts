import { readRefusal } from 'fleet/core';
import { CadenceAnchor as WireCadenceAnchor, type ChoreView } from 'fleet/hub-api';
import {
  gardenCadenceAttentionView,
  gardenCadenceErrandView,
  gardenCadenceView,
  gardenChoreView,
  gardenErrandRunView,
} from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { CADENCE_ANCHORS, EMPTY_CADENCE_DRAFT } from './cadence.domain';
import {
  CADENCE_REFUSALS,
  choreDocument,
  choreDraftDocument,
  choresAdding,
  choresRemoving,
  choresReplacing,
  fromCadenceErrandView,
  fromCadenceView,
  spacingDraftText,
  choreRefusals,
  toChoreDraft,
  toChoresBaseline,
  toCreateRequest,
  toDraft,
  toEditBaseline,
  toEditRequest,
} from './cadence.translate';

const nightlyView = gardenChoreView();
/** A chore whose mode and urgency this build does not list — it must travel back verbatim. */
const oddView = gardenChoreView({
  chore_id: 'chr_2',
  key: 'odd',
  routine: 'docs',
  scopes: ['web', 'hub'],
  mode: 'sampled' as ChoreView['mode'],
  promote: false,
  note: 'keep',
  urgency: 'someday',
});
const held = fromCadenceView(gardenCadenceView({ slug: 'evening', chores: [nightlyView, oddView] }));
const [NIGHTLY, ODD] = held.chores;
const nightlyDoc = { key: 'nightly', routine: 'nightly', scopes: 'all', mode: 'full', promote: true, note: null, urgency: null };
const oddDoc = { key: 'odd', routine: 'docs', scopes: ['web', 'hub'], mode: 'sampled', promote: false, note: 'keep', urgency: 'someday' };

describe('the domain anchors', () => {
  it('are the wire anchors, in order', () => {
    expect([...CADENCE_ANCHORS]).toEqual(Object.values(WireCadenceAnchor));
  });
});

describe('fromCadenceView', () => {
  it('reads every field of a served cadence', () => {
    const wire = gardenCadenceView({
      slug: 'evening',
      project_id: 'prj_1',
      project_name: 'Acme',
      project_slug: 'acme',
      anchor: 'finished',
      chores: [nightlyView],
      attention: [gardenCadenceAttentionView()],
      errand_counts: { due: 1, running: 2, waiting: 3, stopped: 4 },
      revision: 7,
    });
    expect(fromCadenceView(wire)).toEqual({
      id: 'cad_evening',
      revision: 7,
      slug: 'evening',
      name: 'evening',
      projectId: 'prj_1',
      projectName: 'Acme',
      projectSlug: 'acme',
      schedule: { cron: '0 20 * * *', zone: 'America/Chicago' },
      spacingMinutes: 14400,
      anchor: 'finished',
      chores: [
        {
          id: 'chr_1',
          key: 'nightly',
          routine: 'nightly',
          scopes: 'all',
          mode: 'full',
          promote: true,
          note: null,
          urgency: null,
          scopesSetAt: nightlyView.scopes_set_at,
        },
      ],
      attention: [{ chore: 'nightly', scope: 'gone', reason: 'scope_unknown' }],
      errandCounts: { due: 1, running: 2, waiting: 3, stopped: 4 },
      nextMoments: wire.next_moments,
      retired: false,
      createdAt: wire.created_at,
    });
  });

  it('carries a vocabulary value this build does not know as unlisted, raw', () => {
    const cadence = fromCadenceView(
      gardenCadenceView({
        slug: 'odd',
        anchor: 'sometime' as never,
        chores: [oddView],
        attention: [gardenCadenceAttentionView({ reason: 'mystery' as never })],
      }),
    );
    expect(cadence.anchor).toEqual({ unlisted: 'sometime' });
    expect(cadence.chores[0].mode).toEqual({ unlisted: 'sampled' });
    expect(cadence.attention[0].reason).toEqual({ unlisted: 'mystery' });
  });

  it('reads no schedule and no anchor as null', () => {
    const cadence = fromCadenceView(gardenCadenceView({ slug: 'bare', schedule: null, spacing_minutes: null }));
    expect(cadence.schedule).toBeNull();
    expect(cadence.spacingMinutes).toBeNull();
    expect(cadence.anchor).toBeNull();
  });
});

describe('fromCadenceErrandView', () => {
  it('carries the chore, scope, state, attention, last run, and due moment', () => {
    const run = gardenErrandRunView();
    expect(
      fromCadenceErrandView(
        gardenCadenceErrandView({
          state: 'stopped',
          last_run: run,
          due_at: null,
          attention: [gardenCadenceAttentionView({ reason: 'scope_unknown' })],
        }),
      ),
    ).toEqual({
      chore: 'nightly',
      scope: 'blizzard',
      state: 'stopped',
      attention: [{ chore: 'nightly', scope: 'gone', reason: 'scope_unknown' }],
      lastRun: { chunkId: run.chunk_id, scheduledAt: run.scheduled_at },
      dueAt: null,
    });
  });

  it('holds a state this build does not know as unlisted, and a never-run errand as no last run', () => {
    const errand = fromCadenceErrandView(gardenCadenceErrandView({ state: 'parked' as never }));
    expect(errand.state).toEqual({ unlisted: 'parked' });
    expect(errand.lastRun).toBeNull();
  });
});

describe('toDraft', () => {
  it('opens on the cadence’s own rhythm, its spacing as the hub reads it', () => {
    expect(toDraft(fromCadenceView(gardenCadenceView({ slug: 'evening', anchor: 'finished' })))).toEqual({
      slug: 'evening',
      name: 'evening',
      cron: '0 20 * * *',
      zone: 'America/Chicago',
      spacing: '10d',
      anchor: 'finished',
    });
  });

  it('opens an unstated or unlisted anchor as unstated, and no schedule blank', () => {
    expect(toDraft(held).anchor).toBe('unstated');
    expect(toDraft({ ...held, anchor: { unlisted: 'sometime' } }).anchor).toBe('unstated');
    expect(toDraft({ ...held, schedule: null, spacingMinutes: null })).toMatchObject({ cron: '', zone: '', spacing: '' });
  });
});

describe('spacingDraftText', () => {
  it('names the largest whole unit the hub reads', () => {
    expect(spacingDraftText(1440)).toBe('1d');
    expect(spacingDraftText(360)).toBe('6h');
    expect(spacingDraftText(90)).toBe('90m');
    expect(spacingDraftText(null)).toBe('');
  });
});

describe('toCreateRequest', () => {
  it('sends every field typed, trimmed', () => {
    expect(
      toCreateRequest({
        slug: ' evening ',
        name: 'Evening',
        cron: '0 20 * * *',
        zone: 'America/Chicago',
        spacing: '10d',
        anchor: 'finished',
      }),
    ).toEqual({
      slug: 'evening',
      name: 'Evening',
      schedule: { cron: '0 20 * * *', zone: 'America/Chicago' },
      spacing: '10d',
      anchor: 'finished',
    });
  });

  it('leaves a blank field and an unstated anchor out', () => {
    expect(toCreateRequest({ ...EMPTY_CADENCE_DRAFT, slug: 'daily', spacing: '1d' })).toEqual({ slug: 'daily', spacing: '1d' });
  });

  it('sends a half-typed schedule for the hub to refuse naming the missing half', () => {
    expect(toCreateRequest({ ...EMPTY_CADENCE_DRAFT, slug: 'x', cron: '0 1 * * *' }).schedule).toEqual({
      cron: '0 1 * * *',
      zone: null,
    });
  });
});

describe('toEditRequest', () => {
  const before = toDraft(held);

  it('sends nothing for an unchanged draft', () => {
    expect(toEditRequest(before, { ...before })).toEqual({});
    expect(toEditRequest(before, { ...before, name: ` ${before.name} ` })).toEqual({});
  });

  it('sends only the changed fields, trimmed', () => {
    expect(toEditRequest(before, { ...before, name: ' Evening ' })).toEqual({ name: 'Evening' });
    expect(toEditRequest(before, { ...before, spacing: '2d' })).toEqual({ spacing: '2d' });
    expect(toEditRequest(before, { ...before, anchor: 'scheduled' })).toEqual({ anchor: 'scheduled' });
  });

  it('sends the schedule as a pair when either half changed, null once both are blank', () => {
    expect(toEditRequest(before, { ...before, zone: 'UTC' })).toEqual({ schedule: { cron: '0 20 * * *', zone: 'UTC' } });
    expect(toEditRequest(before, { ...before, cron: '', zone: '' })).toEqual({ schedule: null });
    expect(toEditRequest(before, { ...before, zone: '' })).toEqual({ schedule: { cron: '0 20 * * *', zone: null } });
  });

  it('clears a blank spacing and an unstated anchor with null', () => {
    const anchored = { ...before, anchor: 'finished' as const };
    expect(toEditRequest(anchored, { ...anchored, spacing: '', anchor: 'unstated' })).toEqual({ spacing: null, anchor: null });
  });

  it('never sends an unlisted stored anchor the edit left untouched', () => {
    const unlisted = toDraft({ ...held, anchor: { unlisted: 'sometime' } });
    expect(toEditRequest(unlisted, { ...unlisted, name: 'renamed' })).toEqual({ name: 'renamed' });
  });
});

describe('the whole chore list', () => {
  it('resends a held chore as served, an unlisted mode and urgency included', () => {
    expect(choreDocument(NIGHTLY)).toEqual(nightlyDoc);
    expect(choreDocument(ODD)).toEqual(oddDoc);
  });

  it('adds a chore at the end, carrying the others verbatim', () => {
    expect(
      choresAdding(held.chores, { key: 'weekly', routine: 'deep', scopes: 'a, b c', mode: 'delta', promote: false, note: '', urgency: '' }),
    ).toEqual([
      nightlyDoc,
      oddDoc,
      { key: 'weekly', routine: 'deep', scopes: ['a', 'b', 'c'], mode: 'delta', promote: false, note: null, urgency: null },
    ]);
  });

  it('replaces one chore in place', () => {
    expect(choresReplacing(held.chores, 'nightly', { ...toChoreDraft(NIGHTLY), promote: false })).toEqual([
      { ...nightlyDoc, promote: false },
      oddDoc,
    ]);
  });

  it('removes one chore', () => {
    expect(choresRemoving(held.chores, 'nightly')).toEqual([oddDoc]);
  });

  it('round-trips a held chore through its draft unchanged', () => {
    expect(choreDraftDocument(toChoreDraft(ODD))).toEqual(oddDoc);
    expect(choreDraftDocument(toChoreDraft({ ...NIGHTLY, scopes: null }))).toEqual({ ...nightlyDoc, scopes: null });
  });

  it('clears a blank urgency on the edited chore only', () => {
    expect(choreDraftDocument({ ...toChoreDraft(ODD), urgency: '' }).urgency).toBeNull();
    expect(choresAdding([ODD], toChoreDraft(NIGHTLY))[0].urgency).toBe('someday');
  });
});

describe('toEditBaseline', () => {
  it('captures the id, the revision, and the draft from one read', () => {
    const cadence = fromCadenceView(gardenCadenceView({ cadence_id: 'cad_9', revision: 4, slug: 'evening' }));
    expect(toEditBaseline(cadence)).toEqual({ id: 'cad_9', revision: 4, draft: toDraft(cadence) });
  });
});

describe('CADENCE_REFUSALS', () => {
  const entry = (loc: (string | number)[], msg = 'bad') => ({ detail: [{ loc, msg, type: 'value_error' }] });

  it.each([
    [['body', 'schedule', 'cron'], 'cron'],
    [['body', 'schedule.cron'], 'cron'],
    [['body', 'schedule', 'zone'], 'zone'],
    [['body', 'schedule'], 'cron'],
    [['body', 'spacing'], 'spacing'],
    [['body', 'anchor'], 'anchor'],
    [['body', 'slug'], 'slug'],
    [['body', 'name'], 'name'],
  ])('lands a refusal at %j on the %s field', (loc, field) => {
    expect(readRefusal(entry(loc), CADENCE_REFUSALS)).toEqual({ ok: false, fields: { [field]: 'bad' }, form: null });
  });

  it('sends a path no field owns, or an inherited key, to the form line', () => {
    expect(readRefusal(entry(['body', 'chores']), CADENCE_REFUSALS)).toEqual({ ok: false, fields: {}, form: 'chores: bad' });
    expect(CADENCE_REFUSALS.field('constructor')).toBeNull();
  });

  it('lands a slug already taken on the slug field', () => {
    const taken = { type: 'urn:blizzard:problem:cadence-slug-taken', status: 422, detail: 'the project already holds slug evening' };
    expect(readRefusal(taken, CADENCE_REFUSALS)).toEqual({
      ok: false,
      fields: { slug: 'the project already holds slug evening' },
      form: null,
    });
  });

  it('keeps a revision conflict on the form line', () => {
    const conflict = { type: 'urn:blizzard:problem:revision-conflict', status: 409, detail: 'the cadence changed since it was read' };
    expect(readRefusal(conflict, CADENCE_REFUSALS)).toEqual({ ok: false, fields: {}, form: 'the cadence changed since it was read' });
  });
});

describe('a chore save', () => {
  it('captures a baseline detached from a later read', () => {
    const baseline = toChoresBaseline(held);
    expect(baseline).toEqual({ id: held.id, revision: held.revision, draft: held.chores });
    expect(baseline.draft).not.toBe(held.chores);
  });

  it('opens an unspecified urgency as the routine default', () => {
    expect(toChoreDraft(NIGHTLY).urgency).toBe('');
    expect(toChoreDraft(ODD).urgency).toBe('someday');
  });

  it('keeps the target key on an edit whatever the draft holds', () => {
    expect(choresReplacing(held.chores, 'nightly', { ...toChoreDraft(NIGHTLY), key: 'renamed' })[0].key).toBe('nightly');
  });

  it("maps the target chore's fields and scope entries, and nothing else", () => {
    const map = choreRefusals(1);
    expect(map.field('chores[1].routine')).toBe('routine');
    expect(map.field('chores[1].key')).toBe('key');
    expect(map.field('chores[1].scopes[2]')).toBe('scopes');
    expect(map.field('chores[0].routine')).toBeNull();
    expect(map.field('chores[1].unknown')).toBeNull();
    expect(map.field('chores')).toBeNull();
    expect(map.field('name')).toBeNull();
  });

  it('reads a field 422 on the target onto its field and anything else onto the form line', () => {
    const detail = (loc: unknown[]) => ({ detail: [{ loc, msg: 'bad', type: 'value_error' }] });
    expect(readRefusal(detail(['body', 'chores', 1, 'routine']), choreRefusals(1))).toEqual({ ok: false, fields: { routine: 'bad' }, form: null });
    expect(readRefusal(detail(['body', 'chores', 0, 'routine']), choreRefusals(1))).toEqual({ ok: false, fields: {}, form: 'chores[0].routine: bad' });
  });
});

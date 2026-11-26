import { RunMode } from 'fleet/hub-api';
import {
  deliveredSetDeltaView,
  deliveredSetView,
  routineBaselineView,
  routineRunResponse,
  runCadenceView,
  runDeltaView,
  runEscalationView,
  runRowView,
} from 'fleet/testing';

import { EMPTY_RUN_DRAFT } from './run.domain';
import {
  fromRoutineBaselineView,
  fromRoutineRunResponse,
  fromRunDeltaView,
  toRunRow,
  RUN_REFUSALS,
  toRunRequest,
} from './run.translate';

describe('toRunRequest', () => {
  it('sends the scope, mode, and a trimmed note, and omits an unchosen urgency', () => {
    expect(toRunRequest({ ...EMPTY_RUN_DRAFT, scope: 'web', note: '  look at login  ' })).toEqual({
      scopeSlug: 'web',
      mode: RunMode.FULL,
      note: 'look at login',
    });
  });

  it('sends a blank note as none, delta as delta, and a chosen urgency as its slug', () => {
    expect(toRunRequest({ scope: 'web', mode: 'delta', urgency: 'urgent', note: ' ' })).toEqual({
      scopeSlug: 'web',
      mode: RunMode.DELTA,
      note: null,
      urgency: 'urgent',
    });
  });
});

describe('RUN_REFUSALS', () => {
  it('maps the hub field paths to draft fields, and leaves others to the form line', () => {
    expect(RUN_REFUSALS.field('scope_slug')).toBe('scope');
    expect(RUN_REFUSALS.field('mode')).toBe('mode');
    expect(RUN_REFUSALS.field('urgency')).toBe('urgency');
    expect(RUN_REFUSALS.field('note')).toBe('note');
    expect(RUN_REFUSALS.field('other')).toBeNull();
  });
});

describe('toRunRow', () => {
  it('carries the identity, mode, outcome as a lane status, escalation, and each delivered set’s counts', () => {
    const row = toRunRow(
      runRowView({
        chunk_id: 'ch_1',
        mode: 'delta',
        outcome: 'needs_human',
        escalation: runEscalationView({ node_name: 'survey' }),
        delivered: [deliveredSetView({ finding_set_id: 'fins_1', added_count: 2, observed_count: 1, gone_count: 3 })],
      }),
    );
    expect(row).toEqual({
      chunkId: 'ch_1',
      routineSlug: 'nightly',
      scopeSlug: 'blizzard',
      mode: 'delta',
      mintedAt: expect.any(String),
      laneStatus: { kind: 'needs_human' },
      escalation: { nodeName: 'survey', takeoverCommand: 'blizzard hub chunk takeover ch_1', wrappedTakeoverCommand: '' },
      delivered: [{ addedCount: 2, observedCount: 1, goneCount: 3 }],
    });
  });

  it('holds a mode and an outcome this build does not know as unknown, and no escalation or set as empty', () => {
    const row = toRunRow(runRowView({ chunk_id: 'ch_1', mode: 'sampled' as never, outcome: 'orbiting' as never }));
    expect(row.mode).toEqual({ unlisted: 'sampled' });
    expect(row.laneStatus).toEqual({ kind: 'unknown', raw: 'orbiting' });
    expect(row.escalation).toBeNull();
    expect(row.delivered).toEqual([]);
  });
});

describe('fromRunDeltaView', () => {
  it('carries the cadence, escalation, and each set’s added, observed, and gone groups', () => {
    const delta = fromRunDeltaView(
      runDeltaView({
        chunk_id: 'ch_1',
        cadence: runCadenceView({ chore_key: null }),
        escalation: runEscalationView(),
        sets: [
          deliveredSetDeltaView({
            finding_set_id: 'fins_1',
            revisions: { blizzard: 'abc' },
            measurement: '3 open',
            added: [{ finding_id: null, class: 'dead-code', locus: 'a.ts', summary: 'unused', introduced: null }],
            observed: [{ finding_id: 'fnd_2', class: null, locus: null, summary: null }],
            gone: [{ finding_id: 'fnd_3', note: 'fixed' }],
          }),
        ],
      }),
    );
    expect(delta.cadence).toEqual({ slug: 'evening', name: 'evening', choreKey: null });
    expect(delta.escalation?.nodeName).toBe('survey');
    expect(delta.sets).toEqual([
      {
        findingSetId: 'fins_1',
        revisions: { blizzard: 'abc' },
        measurement: '3 open',
        added: [{ findingId: null, findingClass: 'dead-code', locus: 'a.ts', summary: 'unused', introduced: null }],
        observed: [{ findingId: 'fnd_2', findingClass: null, locus: null, summary: null }],
        gone: [{ findingId: 'fnd_3', note: 'fixed' }],
      },
    ]);
  });

  it('reads a run started by hand, unescalated, with no sets', () => {
    const delta = fromRunDeltaView(runDeltaView({ chunk_id: 'ch_1' }));
    expect(delta).toEqual({ chunkId: 'ch_1', routineSlug: 'nightly', scopeSlug: 'blizzard', escalation: null, cadence: null, sets: [] });
  });
});

describe('fromRoutineBaselineView', () => {
  it('carries the scope, set, recorded moment, and each repo pinned', () => {
    const baseline = fromRoutineBaselineView(
      routineBaselineView({ scope_slug: 'web', repos: [{ repo: 'blizzard', revision: 'abc', landed_since: 4 }] }),
    );
    expect(baseline).toEqual({
      scopeSlug: 'web',
      findingSetId: 'fins_1',
      recordedAt: expect.any(String),
      repos: [{ repo: 'blizzard', revision: 'abc', landedSince: 4 }],
    });
  });
});

describe('fromRoutineRunResponse', () => {
  it('carries the minted chunk and whether the run was downgraded', () => {
    expect(fromRoutineRunResponse(routineRunResponse({ chunk_id: 'ch_1', downgraded: true }))).toEqual({
      chunkId: 'ch_1',
      downgraded: true,
    });
  });
});

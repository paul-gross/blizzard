// exemplar: bzh:frontend-containers-compose, bzh:frontend-open-vocabulary — each derivation proved as a pure function, no TestBed.

import { type PermitState, type PermitView } from 'fleet';
import { permitProjectLabel, permitsLabel, withPendingPermitChanges } from './permits-page.model';

const REQUESTED: PermitView = {
  permit_id: 'pmt_1',
  project_id: 'prj_core',
  project_slug: 'core',
  project_name: 'Core',
  holder: 'ada',
  capability: 'deploy',
  state: 'requested',
  verbs: ['grant', 'revoke'],
  requested_at: '2026-10-01T09:00:00Z',
  requested_by: 'usr_grace',
  changed_at: null,
  changed_by: null,
  revocation_note: null,
};

const GRANTED: PermitView = { ...REQUESTED, permit_id: 'pmt_2', state: 'granted', verbs: ['suspend', 'revoke'] };

describe('permitsLabel', () => {
  it('names the holder the listing is narrowed to', () => {
    expect(permitsLabel('ada')).toBe('Permits held by ada');
  });

  it('is plain with no holder named', () => {
    expect(permitsLabel(null)).toBe('Permits');
  });
});

describe('permitProjectLabel', () => {
  it("prefers the project's name", () => {
    expect(permitProjectLabel(REQUESTED)).toBe('Core');
  });

  it('falls back to the slug, then the id', () => {
    expect(permitProjectLabel({ ...REQUESTED, project_name: null })).toBe('core');
    expect(permitProjectLabel({ ...REQUESTED, project_name: null, project_slug: null })).toBe('prj_core');
  });

  it('is empty when the hub knows no label at all', () => {
    expect(permitProjectLabel({ ...REQUESTED, project_id: null, project_slug: null, project_name: null })).toBe('');
  });
});

describe('withPendingPermitChanges', () => {
  it('passes each permit through in its own state while nothing is in flight', () => {
    expect(withPendingPermitChanges([REQUESTED], [], [])).toEqual([{ permit: REQUESTED, state: 'requested', pending: false }]);
  });

  it('passes a state this build does not know through raw', () => {
    const lapsed = { ...REQUESTED, state: 'lapsed' as PermitState };

    expect(withPendingPermitChanges([lapsed], [], [])).toEqual([{ permit: lapsed, state: 'lapsed', pending: false }]);
  });

  it('shows a pending move as the state it enters', () => {
    const [row] = withPendingPermitChanges([REQUESTED], [{ permitId: 'pmt_1', verb: 'grant' }], []);

    expect(row).toEqual({ permit: REQUESTED, state: 'granted', pending: true });
  });

  it('shows a pending revocation as revoked', () => {
    const [row] = withPendingPermitChanges([GRANTED], [], [{ permitId: 'pmt_2', note: 'left the team' }]);

    expect(row.state).toBe('revoked');
    expect(row.pending).toBe(true);
  });

  it('leaves a sibling of a pending permit untouched', () => {
    const rows = withPendingPermitChanges([REQUESTED, GRANTED], [{ permitId: 'pmt_2', verb: 'suspend' }], []);

    expect(rows.map((row) => [row.state, row.pending])).toEqual([
      ['requested', false],
      ['suspended', true],
    ]);
  });
});

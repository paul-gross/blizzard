import { RunMode } from 'fleet';

import { EMPTY_RUN_DRAFT } from './run.domain';
import { RUN_REFUSALS, toRunRequest } from './run.translate';

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

import { readRefusal } from 'fleet';

import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { type FindingTriageDraft } from './finding-triage.domain';
import {
  FINDING_TRIAGE_REFUSALS,
  toExitVars,
  toSupersedeVars,
} from './finding-triage.translate';

const draft = (verb: FindingTriageVerb): FindingTriageDraft => ({ verb, note: '  a note ', supersededBy: ' fnd_9 ' });

describe('finding triage vars', () => {
  it.each(['resolve', 'confirm-gone', 'wont-fix', 'not-a-finding', 'reopen'] as const)(
    '%s sends the trimmed note and never the absorbing id',
    (verb) => {
      expect(toExitVars(['fnd_1', 'fnd_2'], draft(verb))).toEqual({ findingIds: ['fnd_1', 'fnd_2'], note: 'a note' });
    },
  );

  it('supersede also sends the trimmed absorbing id', () => {
    expect(toSupersedeVars(['fnd_1'], draft('supersede'))).toEqual({
      findingIds: ['fnd_1'],
      note: 'a note',
      supersededBy: 'fnd_9',
    });
  });
});

describe('FINDING_TRIAGE_REFUSALS', () => {
  const read = (error: unknown) => readRefusal(error, FINDING_TRIAGE_REFUSALS, 'resolve failed.');

  it('lands the note and superseded_by validation paths on their fields', () => {
    const out = read({
      detail: [
        { loc: ['body', 'note'], msg: 'blank' },
        { loc: ['body', 'superseded_by'], msg: 'not live' },
      ],
    });
    expect(out).toEqual({ ok: false, fields: { note: 'blank', supersededBy: 'not live' }, form: null });
  });

  it.each([
    ['finding-note-required', 'note'],
    ['finding-supersedes-itself', 'supersededBy'],
    ['absorber-not-live', 'supersededBy'],
  ])('lands %s on %s', (kind, field) => {
    const out = read({ type: `urn:blizzard:problem:${kind}`, detail: 'refused' });
    expect(out).toEqual({ ok: false, fields: { [field]: 'refused' }, form: null });
  });

  it.each(['finding-not-found', 'finding-transition-refused', 'finding-write-contended', 'duplicate-finding'])(
    'lands %s on the form line',
    (kind) => {
      expect(read({ type: `urn:blizzard:problem:${kind}`, detail: 'refused' })).toEqual({
        ok: false,
        fields: {},
        form: 'refused',
      });
    },
  );

  it('lands an unmapped path on the form line, and a bodiless failure on the verb fallback', () => {
    expect(read({ detail: [{ loc: ['body', 'finding_ids'], msg: 'bad' }] })).toEqual({
      ok: false,
      fields: {},
      form: 'finding_ids: bad',
    });
    expect(read(new TypeError('network'))).toEqual({ ok: false, fields: {}, form: 'resolve failed.' });
  });
});

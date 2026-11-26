// exemplar: bzh:frontend-domain-boundary, bzh:frontend-edit-baseline — the translator asserted at both edges.

import { readRefusal } from 'fleet';

import { acceptanceDraftFor } from './proposal-acceptance.domain';
import { PROPOSAL_ACCEPTANCE_REFUSALS, toAcceptVars } from './proposal-acceptance.translate';

describe('toAcceptVars', () => {
  it('sends a mint with every optional field empty as the mint flag alone', () => {
    expect(toAcceptVars('gp_1', { ...acceptanceDraftFor('  '), reason: ' ' })).toEqual({
      proposalId: 'gp_1',
      mintWorkItem: true,
    });
  });

  it('sends a mint with the trimmed body and reason and the chosen urgency', () => {
    expect(
      toAcceptVars('gp_1', { mode: 'mint', body: ' the body ', reason: ' agreed ', urgency: 'urgent' }),
    ).toEqual({ proposalId: 'gp_1', mintWorkItem: true, body: 'the body', reason: 'agreed', urgency: 'urgent' });
  });

  it('keeps the tenant default by sending no urgency', () => {
    expect('urgency' in toAcceptVars('gp_1', acceptanceDraftFor('b'))).toBe(false);
  });

  it('sends a decline with only the trimmed reason, never a stale body or urgency', () => {
    expect(
      toAcceptVars('gp_1', { mode: 'decline', body: 'stale body', reason: ' tracked elsewhere ', urgency: 'urgent' }),
    ).toEqual({ proposalId: 'gp_1', mintWorkItem: false, reason: 'tracked elsewhere' });
  });
});

describe('PROPOSAL_ACCEPTANCE_REFUSALS', () => {
  const read = (error: unknown) => readRefusal(error, PROPOSAL_ACCEPTANCE_REFUSALS, 'Accept failed.');

  it('lands each field path on its field', () => {
    for (const field of ['body', 'reason', 'urgency'] as const) {
      expect(read({ detail: [{ loc: ['body', field], msg: 'refused' }] })).toEqual({
        ok: false,
        fields: { [field]: 'refused' },
        form: null,
      });
    }
  });

  it('lands a blank minted body on the body field', () => {
    expect(read({ type: 'urn:blizzard:problem:garden-proposal-invalid', detail: 'body is blank' })).toEqual({
      ok: false,
      fields: { body: 'body is blank' },
      form: null,
    });
  });

  it('lands a raced closure and an ingest conflict on the form line', () => {
    expect(read({ type: 'urn:blizzard:problem:garden-proposal-already-closed', detail: 'closed' })).toEqual({
      ok: false,
      fields: {},
      form: 'closed',
    });
    expect(read({ type: 'urn:blizzard:problem:pointer-already-held', detail: 'raced' })).toEqual({
      ok: false,
      fields: {},
      form: 'raced',
    });
  });
});

import { readRefusal } from 'fleet';

import { PROPOSAL_PASS_REFUSALS, toPassVars } from './proposal-pass.translate';

describe('toPassVars', () => {
  it('sends the trimmed reason with the proposal id', () => {
    expect(toPassVars('gp_1', { reason: '  not yet ' })).toEqual({
      proposalId: 'gp_1',
      reason: 'not yet',
    });
  });
});

describe('PROPOSAL_PASS_REFUSALS', () => {
  const read = (error: unknown) => readRefusal(error, PROPOSAL_PASS_REFUSALS, 'Pass failed.');

  it('lands the reason validation path and the invalid problem on the reason field', () => {
    expect(read({ detail: [{ loc: ['body', 'reason'], msg: 'blank' }] })).toEqual({
      ok: false,
      fields: { reason: 'blank' },
      form: null,
    });
    expect(
      read({
        type: 'urn:blizzard:problem:garden-proposal-invalid',
        detail: 'blank',
      }),
    ).toEqual({
      ok: false,
      fields: { reason: 'blank' },
      form: null,
    });
  });

  it('lands a raced closure on the form line', () => {
    expect(
      read({
        type: 'urn:blizzard:problem:garden-proposal-already-closed',
        detail: 'closed',
      }),
    ).toEqual({
      ok: false,
      fields: {},
      form: 'closed',
    });
  });
});

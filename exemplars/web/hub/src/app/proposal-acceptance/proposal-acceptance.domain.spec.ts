// exemplar: bzh:frontend-domain-boundary — the opening draft and the decline rule asserted.

import { acceptanceDraftFor, isDeclineReasonMissing } from './proposal-acceptance.domain';

describe('acceptanceDraftFor', () => {
  it('opens as a mint of the proposal body, with no reason and the tenant default urgency', () => {
    expect(acceptanceDraftFor('the body')).toEqual({ mode: 'mint', body: 'the body', reason: '', urgency: '' });
  });
});

describe('isDeclineReasonMissing', () => {
  it('is owed only by a decline whose reason is blank or whitespace', () => {
    expect(isDeclineReasonMissing({ mode: 'decline', reason: '' })).toBe(true);
    expect(isDeclineReasonMissing({ mode: 'decline', reason: '  ' })).toBe(true);
    expect(isDeclineReasonMissing({ mode: 'decline', reason: 'tracked' })).toBe(false);
    expect(isDeclineReasonMissing({ mode: 'mint', reason: '' })).toBe(false);
  });
});

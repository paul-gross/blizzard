// exemplar: bzh:frontend-domain-boundary — the opening draft asserted.

import { acceptanceDraftFor } from './proposal-acceptance.domain';

describe('acceptanceDraftFor', () => {
  it('opens as a mint of the proposal body, with no reason and the tenant default urgency', () => {
    expect(acceptanceDraftFor('the body')).toEqual({ mode: 'mint', body: 'the body', reason: '', urgency: '' });
  });
});

import { describe, expect, it } from 'vitest';
import { providerSummary } from 'fleet/testing';

import { fromProviderSummary } from './provider.translate';

describe('fromProviderSummary', () => {
  it('carries the name, label and kind, an unforeseen kind included', () => {
    expect(fromProviderSummary(providerSummary({ name: 'corp', display_name: 'Corp SSO', type: 'saml-next' }))).toEqual({
      name: 'corp',
      display_name: 'Corp SSO',
      type: 'saml-next',
    });
  });
});

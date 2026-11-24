import { describe, expect, it } from 'vitest';
import { hostRegistrationView } from 'fleet/testing';

import { fromHostRegistrationView } from './host-registration.translate';

describe('fromHostRegistrationView', () => {
  it('carries a known state and the facts it names', () => {
    expect(
      fromHostRegistrationView(
        hostRegistrationView({ state: 'version_refused', host_id: 'hs_1', held_runner_ids: ['rn_1'], detail: 'upgrade', answered_at: 't' }),
      ),
    ).toEqual({
      state: 'version_refused',
      host_id: 'hs_1',
      host_name: null,
      tenant_id: null,
      held_runner_ids: ['rn_1'],
      buffered_facts: 0,
      detail: 'upgrade',
      answered_at: 't',
    });
  });

  it('reads a state this build does not know as unknown, keeping its spelling', () => {
    const wire = { ...hostRegistrationView(), state: 'suspended' } as unknown as Parameters<typeof fromHostRegistrationView>[0];
    expect(fromHostRegistrationView(wire).state).toEqual({ kind: 'unknown', raw: 'suspended' });
  });
});

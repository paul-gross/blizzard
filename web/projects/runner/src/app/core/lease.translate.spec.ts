import { describe, expect, it } from 'vitest';
import { runnerLeaseView } from 'fleet/testing';

import { fromLeaseView } from './lease.translate';

describe('fromLeaseView', () => {
  it('carries the wire judgments from their fields', () => {
    const lease = fromLeaseView(
      runnerLeaseView({
        state: 'closed',
        display_status: 'waiting-ask',
        liveness: 'ended',
        closure_healthy: false,
        closure_reason: 'reaped',
      }),
    );
    expect(lease.state).toBe('closed');
    expect(lease.display_status).toBe('waiting-ask');
    expect(lease.liveness).toBe('ended');
    expect(lease.closure_healthy).toBe(false);
    expect(lease.closure_reason).toBe('reaped');
  });

  it('carries liveness as the wire reads it, not re-derived from state', () => {
    expect(fromLeaseView(runnerLeaseView({ state: 'running', liveness: 'ended' })).liveness).toBe('ended');
  });

  it('maps unforeseen vocabulary onto explicit unknowns', () => {
    const lease = fromLeaseView({
      ...runnerLeaseView(),
      state: 'hibernating' as never,
      display_status: 'dreaming' as never,
      liveness: 'undead' as never,
      closure_reason: 'evicted',
    });
    expect(lease.state).toEqual({ kind: 'unknown', raw: 'hibernating' });
    expect(lease.display_status).toEqual({ kind: 'unknown', raw: 'dreaming' });
    expect(lease.liveness).toBe('unknown');
    expect(lease.closure_reason).toEqual({ kind: 'unknown', raw: 'evicted' });
  });

  it('keeps an absent closure reason absent', () => {
    expect(fromLeaseView(runnerLeaseView()).closure_reason).toBeNull();
  });
});

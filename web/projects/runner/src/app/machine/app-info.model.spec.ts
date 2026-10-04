import type { runnerApi } from 'fleet';

import { lastFlushLabel, lastTickLabel, wellFormedRunner } from './app-info.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

function runner(overrides: Partial<runnerApi.RunnerStatusView> = {}): runnerApi.RunnerStatusView {
  return {
    runner_id: 'runner-local',
    workspace_id: 'workspace-local',
    pause: { local: false, hub: false, effective: false },
    capacities: { max_agents: 4, used: 1, free: 3 },
    hub: {
      endpoint: 'http://127.0.0.1:8421',
      reachable: true,
      last_contact_at: '2026-07-16T11:59:30.000Z',
      buffer_depth: 2,
    },
    last_tick_at: '2026-07-16T11:59:45.000Z',
    ...overrides,
  };
}

describe('wellFormedRunner', () => {
  it('passes a well-formed runner section through', () => {
    const data = runner();
    expect(wellFormedRunner(data)).toBe(data);
  });

  it('returns null for an absent section', () => {
    expect(wellFormedRunner(undefined)).toBeNull();
    expect(wellFormedRunner(null)).toBeNull();
  });

  it('returns null for a malformed body missing hub, capacities, or pause', () => {
    expect(wellFormedRunner({} as runnerApi.RunnerStatusView)).toBeNull();
    for (const key of ['hub', 'capacities', 'pause'] as const) {
      const malformed: Partial<runnerApi.RunnerStatusView> = runner();
      delete malformed[key];
      expect(wellFormedRunner(malformed as runnerApi.RunnerStatusView)).toBeNull();
    }
  });
});

describe('lastFlushLabel', () => {
  it('renders the age of the last successful contact', () => {
    expect(lastFlushLabel(runner(), NOW)).toBe('-30s');
  });

  it('renders never before first contact or with no runner section', () => {
    const view = runner();
    expect(lastFlushLabel({ ...view, hub: { ...view.hub, last_contact_at: null } }, NOW)).toBe('never');
    expect(lastFlushLabel(null, NOW)).toBe('never');
  });

  it('renders an em dash for a skew-broken timestamp', () => {
    const view = runner();
    expect(lastFlushLabel({ ...view, hub: { ...view.hub, last_contact_at: '2026-07-16T13:00:00.000Z' } }, NOW)).toBe(
      '—',
    );
  });
});

describe('lastTickLabel', () => {
  it('renders the age of the last loop tick', () => {
    expect(lastTickLabel(runner(), NOW)).toBe('-15s');
  });

  it('renders an em dash before the first tick, with no runner section, or past the skew bound', () => {
    expect(lastTickLabel(runner({ last_tick_at: null }), NOW)).toBe('—');
    expect(lastTickLabel(null, NOW)).toBe('—');
    expect(lastTickLabel(runner({ last_tick_at: '2026-07-16T13:00:00.000Z' }), NOW)).toBe('—');
  });
});

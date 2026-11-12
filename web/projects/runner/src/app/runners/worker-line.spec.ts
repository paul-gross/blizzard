import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';

import { WorkerLine } from './worker-line';

const LEASE: runnerApi.LeaseView = {
  lease_id: 'lease_1', chunk_id: 'ch_1', graph_id: 'gr_1', node_id: 'nd_build', node_name: 'build',
  epoch: 1, session_id: 'sess_1', pid: 1234, environment_id: 'a-long-environment-name', workdir: '/ws/env',
  created_at: '2026-07-16T11:00:00.000Z', last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running', liveness: 'live', display_status: 'running', closure_healthy: false,
  closed_at: null, closure_reason: null, stale_after_seconds: 3600, resolved_model: null,
  resolved_effort: null, harness_version: null, git_commits: [], harness_id: null, project_id: null,
};

describe('WorkerLine', () => {
  it('keeps card facts truncated by default and opts into wrapping and explicit Env copy only for the drawer', async () => {
    await TestBed.configureTestingModule({
      imports: [WorkerLine],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(WorkerLine);
    fixture.componentRef.setInput('lease', LEASE);
    fixture.componentRef.setInput('showNode', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.wl')?.classList).not.toContain('wrapping');
    expect(el.querySelector('.facts')?.classList).toContain('env-only');
    expect(el.querySelector('[data-testid="worker-env"]')?.textContent?.trim()).toBe(LEASE.environment_id);

    fixture.componentRef.setInput('wrapping', '');
    await fixture.whenStable();
    expect(el.querySelector('.wl')?.classList).toContain('wrapping');
    expect(el.querySelector('.facts')?.classList).not.toContain('env-only');
    expect(el.querySelector('[data-testid="worker-env"]')?.textContent?.trim()).toBe(`Env: ${LEASE.environment_id}`);
    expect(el.querySelector('[data-testid="worker-node"]')).toBeNull();

    fixture.componentRef.setInput('wrapping', false);
    fixture.componentRef.setInput('showNode', true);
    fixture.componentRef.setInput('lease', { ...LEASE, environment_id: null, state: 'parked' });
    await fixture.whenStable();
    expect(el.querySelector('.wl')?.classList).not.toContain('wrapping');
    expect(el.querySelector('[data-testid="worker-env"]')?.textContent?.trim()).toBe('no environment');
    expect(el.querySelector('[data-testid="worker-node"]')?.textContent).toBe('build');
    expect(el.querySelector('[data-testid="worker-state"]')?.textContent?.trim()).toBe('PARKED');
  });
});

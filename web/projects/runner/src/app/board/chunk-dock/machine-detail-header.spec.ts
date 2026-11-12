import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { runnerApi } from 'fleet';
import { vi } from 'vitest';

import { MachineDetailHeader } from './machine-detail-header';

const REF = Date.parse('2026-07-16T12:00:00.000Z');

/** A closed lease: no live worker, ended at `closedAt`. */
function endedLease(closedAt: string): runnerApi.LeaseView {
  return {
    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
    chunk_id: 'ch_01dockage00000000000000000',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: 'sess-1',
    pid: null,
    environment_id: 'e1',
    workdir: '/ws/e1',
    created_at: '2026-07-16T10:00:00.000Z',
    last_heartbeat_at: null,
    state: 'closed',
    liveness: 'ended',
    display_status: 'closed',
    closure_healthy: true,
    closed_at: closedAt,
    closure_reason: null,
    stale_after_seconds: 3600,
    resolved_model: null,
    resolved_effort: null,
    harness_version: null,
    git_commits: [],
    harness_id: null,
    project_id: null,
  } as runnerApi.LeaseView;
}

async function noWorkerNote(closedAt: string): Promise<string> {
  await TestBed.configureTestingModule({
    imports: [MachineDetailHeader],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetailHeader);
  fixture.componentRef.setInput('chunkId', 'ch_01dockage00000000000000000');
  fixture.componentRef.setInput('lease', endedLease(closedAt));
  fixture.componentRef.setInput('worker', null);
  fixture.detectChanges();
  return (fixture.nativeElement as HTMLElement).textContent ?? '';
}

describe('MachineDetailHeader ended-ago note', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(REF);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders the age of a valid past closed_at', async () => {
    const text = await noWorkerNote('2026-07-16T11:55:00.000Z');
    expect(text).toContain('No live worker — last session ended 5m ago');
  });

  it('renders no ago clause for an unparseable stamp', async () => {
    const text = await noWorkerNote('not-a-date');
    expect(text).toContain('No live worker — last session ended');
    expect(text).not.toContain('ago');
    expect(text).not.toContain('NaN');
  });

  it('renders no ago clause for a stamp ahead of now beyond the skew tolerance', async () => {
    const text = await noWorkerNote('2026-07-16T12:10:00.000Z');
    expect(text).toContain('No live worker — last session ended');
    expect(text).not.toContain('ago');
  });

  it('renders a zero age for a stamp ahead of now within the skew tolerance', async () => {
    const text = await noWorkerNote('2026-07-16T12:00:30.000Z');
    expect(text).toContain('last session ended 0s ago');
  });
});

describe('MachineDetailHeader identity', () => {
  for (const displayStatus of ['waiting-ask', 'running'] as const) {
    it(`keeps project first, the ${displayStatus} status distinct from Node, and worker wrapping drawer-only`, async () => {
      await TestBed.configureTestingModule({
        imports: [MachineDetailHeader],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();
      const fixture = TestBed.createComponent(MachineDetailHeader);
      const worker = { ...endedLease('2026-07-16T11:55:00.000Z'), display_status: displayStatus, state: 'running', pid: 1234 };
      fixture.componentRef.setInput('chunkId', worker.chunk_id);
      fixture.componentRef.setInput('projectName', 'Console');
      fixture.componentRef.setInput('runnerName', 'r1');
      fixture.componentRef.setInput('nodeName', 'build');
      fixture.componentRef.setInput('statusLabel', 'RUNNING');
      fixture.componentRef.setInput('statusTone', 'running');
      fixture.componentRef.setInput('workRefs', [{ source: 'test', ref: '123', label: 'test#123', web_url: null }]);
      fixture.componentRef.setInput('lease', worker);
      fixture.componentRef.setInput('worker', worker);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('.d-sub')?.firstElementChild?.getAttribute('data-testid')).toBe('detail-project');
      expect(el.querySelector('[data-testid="machine-detail-status"]')?.textContent?.trim()).toBe(displayStatus === 'waiting-ask' ? 'Waiting on human' : 'RUNNING');
      expect(el.querySelector('[data-testid="detail-node"]')?.textContent?.trim()).toBe('Node: build');
      expect(el.querySelector('[data-testid="worker-node"]')).toBeNull();
      expect(el.querySelector('[data-testid="worker-line"]')?.classList).toContain('wrapping');
      expect(el.querySelector('[data-testid="worker-env"]')?.textContent?.trim()).toBe('Env: e1');
      expect(el.querySelector('[data-testid="detail-worker-pid"]')?.textContent?.trim()).toBe('pid 1234');
    });
  }
});

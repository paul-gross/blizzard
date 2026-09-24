import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { page } from 'vitest/browser';

import { MachineDetailView } from './chunk-detail-view';

/**
 * The escalation resume box's harness-provenance badge (blizzard#441), the tooled half
 * of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a real,
 * headless-Chromium proof that the badge renders beside the resume command at the
 * runner's own narrow, mobile-reachable width, with no page error and no horizontal
 * overflow, rather than jsdom's un-laid-out approximation.
 *
 * Excluded from the default `ng test` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const LEASE: runnerApi.LeaseView = {
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNNEW1',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-new',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'closed',
  closed_at: null,
  closure_reason: null,
};

const ESCALATION: runnerApi.EscalationView = {
  chunk_id: LEASE.chunk_id,
  lease_id: LEASE.lease_id,
  node_id: LEASE.node_id,
  epoch: LEASE.epoch,
  closed_at: '2026-07-16T11:00:00.000Z',
  resume_command: 'cd /ws/beta && claude --resume sess-new',
  harness_id: 'claude_code',
  harness_version: '1.2.3',
};

describe('local-panel escalation harness-provenance layout shell sweep (web:shell-sweep, blizzard#441)', () => {
  it('renders the harness badge beside the resume command with no page errors or horizontal overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [MachineDetailView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(MachineDetailView);
    fixture.componentRef.setInput('lease', LEASE);
    fixture.componentRef.setInput('escalation', ESCALATION);
    fixture.componentRef.setInput('leaseRef', 'L-EW1');
    fixture.componentRef.setInput('heartbeatLabel', '-34s');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const box = root.querySelector<HTMLElement>('[data-testid="detail-resume"]')!;
      expect(box).not.toBeNull();
      const badge = root.querySelector<HTMLElement>('[data-testid="detail-resume-harness"]')!;
      expect(badge).not.toBeNull();
      expect(badge.getAttribute('data-harness-id')).toBe('claude_code');
      expect(badge.textContent?.trim()).toBe('claude code');

      expect(
        box.scrollWidth,
        `resume box overflows horizontally at 390px (${box.scrollWidth} > ${box.clientWidth})`,
      ).toBeLessThanOrEqual(box.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

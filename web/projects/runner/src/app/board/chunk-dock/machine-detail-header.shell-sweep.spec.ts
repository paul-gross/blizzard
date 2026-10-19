import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { runnerApi } from 'fleet';
import { commands, page, userEvent } from 'vitest/browser';

import { MachineDetailHeader } from './machine-detail-header';

/**
 * The machine detail dock header's own half of `web:shell-sweep` covers its ⋯ menu's
 * Pause/Resume (`bzh:claim-vocabulary`). `app-panel-mobile.shell-sweep.spec.ts` covers the
 * board's chunk cards, never this header's own action row.
 *
 * Two claims jsdom cannot make: a real pointer click actually opens the menu's CDK overlay
 * and its confirmation naming the runner (`bzh:visual-change-needs-a-render` — a jsdom green
 * proves nothing about a CDK overlay), and the header's own
 * two-cluster row — reached both at the mobile bottom nav's ~390/320px
 * (`app-panel-mobile.html` mounts `app-machine-detail` inside the mobile
 * shell, `bzh:narrow-viewport-tier-rule`) and at `LocalPanelLayout`'s desktop
 * width — never overflows with a long chunk id, project, runner name and worker line live at once.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1024, 390, 320];

/** The design tokens are a global stylesheet the app build loads, never a standalone mount —
 * injected so the status badge's tone colour resolves. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/** The computed colour a design token resolves to, read off a probe element. */
function tokenColor(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

const RUNNER = 'a-long-runner-identity-that-wraps-under-a-narrow-column';

/** The chunk's live worker, holding an environment with a long name. */
const WORKER: runnerApi.LeaseView = {
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: 'ch_01dockwidth0000000000000000',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 3,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'an-environment-with-a-long-name-e12',
  workdir: '/ws/e12',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  display_status: 'running',
  closure_healthy: false,
  closed_at: null,
  closure_reason: null,
  stale_after_seconds: 3600,
};

async function renderHeader(width: number): Promise<{ root: HTMLElement; fixture: ReturnType<typeof TestBed.createComponent<MachineDetailHeader>> }> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [MachineDetailHeader],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetailHeader);
  fixture.componentRef.setInput('chunkId', 'ch_01dockwidth0000000000000000');
  fixture.componentRef.setInput('runnerName', RUNNER);
  fixture.componentRef.setInput('projectName', 'A Project With A Long Display Name');
  fixture.componentRef.setInput('workRefs', [{ source: 'blizzard', ref: '318', label: 'blizzard#318', web_url: null }]);
  fixture.componentRef.setInput('statusLabel', 'RUNNING');
  fixture.componentRef.setInput('statusTone', 'running');
  fixture.componentRef.setInput('nodeName', 'build');
  fixture.componentRef.setInput('lease', WORKER);
  fixture.componentRef.setInput('worker', WORKER);
  fixture.componentRef.setInput('pausable', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return { root, fixture };
}

describe('machine detail header shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps the header's two clusters and its worker line within its own width at width ${width}`, async () => {
      const { root } = await renderHeader(width);
      try {
        // The host renders inline, so the header box itself is what holds the clusters.
        const header = root.querySelector<HTMLElement>('.d-hdr')!;
        expect(header.clientWidth, `width ${width}: the header has no width`).toBeGreaterThan(0);
        expect(
          header.scrollWidth,
          `width ${width}: header overflows horizontally (${header.scrollWidth} > ${header.clientWidth})`,
        ).toBeLessThanOrEqual(header.clientWidth);
        expect(header.getBoundingClientRect().right, `width ${width}: header extends past the viewport`).toBeLessThanOrEqual(window.innerWidth);
        const bounds = header.getBoundingClientRect();
        for (const testid of ['chunk-actions-menu', 'detail-close', 'detail-worker']) {
          const rect = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)?.getBoundingClientRect();
          expect(rect, `width ${width}: no ${testid} in the DOM`).toBeDefined();
          expect(rect!.right, `width ${width}: ${testid} extends past the header (${rect!.right} > ${bounds.right})`).toBeLessThanOrEqual(bounds.right + 1);
        }
      } finally {
        root.remove();
      }
    });
  }

  it('opens the ⋯ menu on a real click, and its Pause asks a confirmation naming the runner', async () => {
    const { root, fixture } = await renderHeader(1024);
    try {
      await userEvent.click(root.querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]')!);
      await fixture.whenStable();
      const pause = document.querySelector<HTMLElement>('[data-testid="pause-chunk"]');
      expect(pause, 'the menu overlay never opened').not.toBeNull();
      expect(pause!.getBoundingClientRect().height).toBeGreaterThan(0);
      expect(pause!.textContent).toContain('Park the agent, keep the claim');

      await userEvent.click(pause!);
      await fixture.whenStable();
      const dialog = document.querySelector<HTMLElement>('[data-testid="confirm-dialog"]');
      expect(dialog?.textContent).toContain(RUNNER);
      document.querySelector<HTMLElement>('[data-testid="confirm-dialog-cancel"]')?.click();
      await fixture.whenStable();
    } finally {
      root.remove();
    }
  });

  for (const width of [1024, 390]) {
    it(`renders the status label as a kit badge in its tone, the node plain beside it, at width ${width}`, async () => {
      await loadDesignTokens();
      const { root } = await renderHeader(width);
      try {
        const status = root.querySelector<HTMLElement>('[data-testid="machine-detail-status"]')!;
        const badge = status.querySelector<HTMLElement>('fleet-kit-badge .badge')!;
        expect(badge.textContent?.trim()).toBe('RUNNING');
        expect(getComputedStyle(badge).color).toBe(tokenColor('--amber'));
        const node = root.querySelector<HTMLElement>('[data-testid="detail-node"]')!;
        expect(node.textContent?.trim()).toBe('build');
        expect(getComputedStyle(node).color).not.toBe(tokenColor('--amber'));
      } finally {
        root.remove();
      }
    });
  }
});

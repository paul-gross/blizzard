import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page, userEvent } from 'vitest/browser';

import { MachineDetailHeader } from './machine-detail-header';

/**
 * The machine detail dock header's own half of `web:shell-sweep` covers
 * {@link KitTooltip} on its Pause/Resume button (`bzh:claim-vocabulary`).
 * `app-panel-mobile.shell-sweep.spec.ts` covers
 * `ChunkCard` line-stacking, never this header's own action row.
 *
 * Two claims jsdom cannot make: a real pointer hover actually opens the
 * tooltip panel with the wired copy text (`bzh:visual-change-needs-a-render`
 * — a jsdom green proves nothing about a CDK overlay), and the header's own
 * two-cluster row — reached both at the mobile bottom nav's ~390/320px
 * (`app-panel-mobile.html` mounts `app-machine-detail` inside the mobile
 * shell, `bzh:narrow-viewport-tier-rule`) and at `LocalPanelLayout`'s desktop
 * width — never overflows with a long chunk id and runner name live at once.
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

async function renderHeader(width: number): Promise<{ root: HTMLElement; fixture: ReturnType<typeof TestBed.createComponent<MachineDetailHeader>> }> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [MachineDetailHeader],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetailHeader);
  fixture.componentRef.setInput('chunkId', 'ch_01dockwidth0000000000000000');
  fixture.componentRef.setInput('runnerName', 'a-long-runner-identity-that-wraps-under-a-narrow-column');
  fixture.componentRef.setInput('statusLabel', 'RUNNING');
  fixture.componentRef.setInput('statusTone', 'running');
  fixture.componentRef.setInput('nodeName', 'build');
  fixture.componentRef.setInput('epoch', 3);
  fixture.componentRef.setInput('pausable', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return { root, fixture };
}

describe('machine detail header shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps the header's two clusters within its own width at width ${width}`, async () => {
      const { root } = await renderHeader(width);
      try {
        expect(
          root.scrollWidth,
          `width ${width}: header overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
        expect(root.querySelector('[data-testid="pause-chunk"]')).not.toBeNull();
        expect(root.querySelector('[data-testid="detail-close"]')).not.toBeNull();
      } finally {
        root.remove();
      }
    });
  }

  it('opens a real hover tooltip on Pause naming the runner, wired through fleetTooltip', async () => {
    const { root } = await renderHeader(1024);
    try {
      const trigger = root.querySelector<HTMLElement>('[data-testid="pause-chunk"]')!;
      await userEvent.unhover(trigger);
      await userEvent.hover(trigger);
      const panel = document.querySelector<HTMLElement>('[role="tooltip"]');
      expect(panel?.textContent).toContain('a-long-runner-identity-that-wraps-under-a-narrow-column');
      await userEvent.unhover(trigger);
    } finally {
      root.remove();
    }
  });

  for (const width of [1024, 390]) {
    it(`renders the status label as a kit badge in its tone, the node suffix plain, at width ${width}`, async () => {
      await loadDesignTokens();
      const { root } = await renderHeader(width);
      try {
        const status = root.querySelector<HTMLElement>('[data-testid="machine-detail-status"]')!;
        const badge = status.querySelector<HTMLElement>('fleet-kit-badge .badge')!;
        expect(badge.textContent?.trim()).toBe('RUNNING');
        expect(getComputedStyle(badge).color).toBe(tokenColor('--amber'));
        expect(status.textContent).toContain('· node build · a3');
        expect(getComputedStyle(status).color).toBe(tokenColor('--label'));
      } finally {
        root.remove();
      }
    });
  }
});

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page, userEvent } from 'vitest/browser';

import { MachineDetailHeader } from './machine-detail-header';

/**
 * The machine detail dock header's own half of `web:shell-sweep` — it had none
 * before this change wired {@link KitTooltip} onto its Pause/Resume button
 * (`bzh:claim-vocabulary`). `local-panel-mobile.shell-sweep.spec.ts` covers
 * `ChunkCard` line-stacking, never this header's own action row.
 *
 * Two claims jsdom cannot make: a real pointer hover actually opens the
 * tooltip panel with the wired copy text (`bzh:visual-change-needs-a-render`
 * — a jsdom green proves nothing about a CDK overlay), and the header's own
 * two-cluster row — reached both at the mobile bottom nav's ~390/320px
 * (`local-panel-mobile.html` mounts `local-machine-detail` inside the mobile
 * shell, `bzh:narrow-viewport-tier-rule`) and at `LocalPanelLayout`'s desktop
 * width — never overflows with a long chunk id and runner name live at once.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1024, 390, 320];

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
});

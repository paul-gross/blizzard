import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import { KitMasterDetail } from './kit-master-detail';

/**
 * {@link KitMasterDetail}'s collapse rule (`bzh:web-shell-sweep`) — a real,
 * headless-Chromium proof of the `@media (min-width: 720px)` row/column flip jsdom
 * parses without ever evaluating.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`) —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
async function nextFrame(): Promise<void> {
  await new Promise((resolve) => requestAnimationFrame(resolve));
}

/** {@link KitMasterDetail}'s `--master-list-col` resolution is the whole claim of the
 * desktop case below, and the design tokens are a global stylesheet loaded via each
 * app's build `styles`, never by a standalone component test — read the sheet's real
 * text server-side and inject it, the same as `hover-tint.shell-sweep.spec.ts`. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

function expectNoOverflow(element: HTMLElement, label: string): void {
  const widest = Array.from(element.querySelectorAll<HTMLElement>('*'))
    .filter((child) => child.scrollWidth > child.clientWidth)
    .map((child) => `${child.tagName}.${child.className} ${child.scrollWidth}/${child.clientWidth}`)
    .join(', ');
  expect(
    element.scrollWidth,
    `${label} overflows horizontally (${element.scrollWidth} > ${element.clientWidth}); children: ${widest}`,
  ).toBeLessThanOrEqual(element.clientWidth);
}

@Component({
  selector: 'fleet-kit-master-detail-sweep-host',
  imports: [KitMasterDetail],
  template: `
    <fleet-kit-master-detail paneId="kmd-sweep" backLabel="Node history" listCaption="Timeline">
      <div kit-master-detail-list data-testid="slot-list">list</div>
      <div kit-master-detail-detail data-testid="slot-detail">detail</div>
    </fleet-kit-master-detail>
  `,
})
class KitMasterDetailSweepHost {}

async function mountSweepHost(): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [KitMasterDetailSweepHost],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(KitMasterDetailSweepHost);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  return root;
}

@Component({
  selector: 'fleet-kit-master-detail-sweep-drill-host',
  imports: [KitMasterDetail],
  template: `
    <fleet-kit-master-detail
      paneId="kmd-drill"
      backLabel="Node history"
      [drilldown]="true"
      [hasSelection]="selected"
      style="height: 300px"
    >
      <div kit-master-detail-list data-testid="slot-list">list</div>
      <div kit-master-detail-detail data-testid="slot-detail" style="flex: 1; min-height: 0; overflow-y: auto">
        <div style="height: 600px">tall</div>
      </div>
    </fleet-kit-master-detail>
  `,
})
class KitMasterDetailSweepDrillHost {
  selected = true;
}

async function mountDrillHost(): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [KitMasterDetailSweepDrillHost],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(KitMasterDetailSweepDrillHost);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  return root;
}

describe('KitMasterDetail collapse shell sweep (web:shell-sweep)', () => {
  it("splits list beside detail at desktop width, the list sized to --master-list-col — proven able to fail by forcing :host's base flex-direction to row", async () => {
    await loadDesignTokens();
    const root = await mountSweepHost();
    try {
      await page.viewport(1024, 700);
      await nextFrame();

      const list = root.querySelector<HTMLElement>('.kmd-list')!;
      const detail = root.querySelector<HTMLElement>('.kmd-detail')!;
      const listRect = list.getBoundingClientRect();
      const detailRect = detail.getBoundingClientRect();

      expect(
        detailRect.left,
        `detail pane's left (${detailRect.left}) sits before the list pane's right (${listRect.right}) — the panes overlap instead of sitting side by side`,
      ).toBeGreaterThanOrEqual(listRect.right);
      // `getBoundingClientRect` includes the pane's own border-right, so the resolved
      // CSS `width` (content-box, excluding that border) is what pins --master-list-col.
      const listWidth = parseFloat(getComputedStyle(list).width);
      expect(listWidth, `list pane's resolved width (${listWidth}) does not match --master-list-col`).toBeCloseTo(320, 0);
    } finally {
      root.remove();
    }
  });

  it('stacks list above detail at phone widths, with no horizontal overflow', async () => {
    await loadDesignTokens();
    const root = await mountSweepHost();
    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 700);
        await nextFrame();

        const list = root.querySelector<HTMLElement>('.kmd-list')!;
        const detail = root.querySelector<HTMLElement>('.kmd-detail')!;
        const listRect = list.getBoundingClientRect();
        const detailRect = detail.getBoundingClientRect();

        expect(listRect.left, `${width}px: list's left (${listRect.left}) and detail's left (${detailRect.left}) are not a common left`).toBeCloseTo(
          detailRect.left,
          0,
        );
        expect(
          detailRect.top,
          `${width}px: detail's top (${detailRect.top}) does not sit below list's top (${listRect.top}) — the panes are side by side instead of stacked`,
        ).toBeGreaterThan(listRect.top);
        expectNoOverflow(root, `${width}px`);
      }
    } finally {
      root.remove();
    }
  });

  it("fills the host with a drill-down's lone detail pane in the 720–767px band and at phone width", async () => {
    await loadDesignTokens();
    const root = await mountDrillHost();
    try {
      const host = root.querySelector<HTMLElement>('fleet-kit-master-detail')!;
      const detail = root.querySelector<HTMLElement>('.kmd-detail')!;
      for (const width of [740, 390]) {
        await page.viewport(width, 700);
        await nextFrame();
        expect(
          detail.getBoundingClientRect().width,
          `${width}px: the lone detail pane does not fill the host (${host.getBoundingClientRect().width})`,
        ).toBeCloseTo(host.getBoundingClientRect().width, 0);
      }
    } finally {
      root.remove();
    }
  });

  it("fills the host with a drill-down's lone list pane in the 720–767px band — proven able to fail by deleting the .kmd-list--only rule inside the media query", async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [KitMasterDetailSweepDrillHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitMasterDetailSweepDrillHost);
    fixture.componentInstance.selected = false;
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      await page.viewport(740, 700);
      await nextFrame();
      const host = root.querySelector<HTMLElement>('fleet-kit-master-detail')!;
      const list = root.querySelector<HTMLElement>('.kmd-list')!;
      expect(list.getBoundingClientRect().width, 'the lone list pane stays at the fixed list column instead of filling the host').toBeCloseTo(
        host.getBoundingClientRect().width,
        0,
      );
      expect(getComputedStyle(root.querySelector<HTMLElement>('.kmd-detail')!).display).toBe('none');
    } finally {
      root.remove();
    }
  });

  it("scrolls a height-bounded slot element inside its pane — proven able to fail by making .kmd-detail display: block", async () => {
    await loadDesignTokens();
    const root = await mountDrillHost();
    try {
      await page.viewport(1024, 700);
      await nextFrame();
      const slot = root.querySelector<HTMLElement>('[data-testid="slot-detail"]')!;
      expect(slot.getBoundingClientRect().height, 'the slot element grew past its pane instead of being bounded by it').toBeLessThanOrEqual(300);
      expect(slot.scrollHeight, 'the slot element does not overflow its own bound').toBeGreaterThan(slot.clientHeight);
    } finally {
      root.remove();
    }
  });
});

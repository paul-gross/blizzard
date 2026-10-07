import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import { KitPanel, KitPanelHeader } from 'fleet';
import { GraphDetailHeader } from './graph-detail-header';

/** The design tokens are a global stylesheet loaded via each app's build `styles`,
 * never by a standalone component test (`board/hover-tint.shell-sweep.spec.ts`'s own
 * precedent) — read the sheet's real text server-side and inject it as a `<style>`
 * element, so the lifecycle badge's `var(--red)`/`var(--cyan)` actually resolve. */
async function loadDesignTokens(): Promise<HTMLStyleElement> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
  return styleEl;
}

/** The computed colour a design token resolves to, read off a throwaway probe. */
function resolvedToken(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

/** `GraphDetail`'s own framing of the header: projected into `KitPanel`'s
 * `fleetKitPanelHeader` slot in supplement mode, beside the panel's name label. */
@Component({
  imports: [KitPanel, KitPanelHeader, GraphDetailHeader],
  template: `
    <fleet-kit-panel label="code-review" [bodyScroll]="false">
      <app-graph-detail-header
        fleetKitPanelHeader
        graphId="g_0123456789ab"
        [retired]="retired()"
        [renderedRetired]="retired()"
        [canEdit]="true"
      />
    </fleet-kit-panel>
  `,
})
class SweepHost {
  readonly retired = signal(false);
}

/**
 * The graph detail header's kit lifecycle badge (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that `graph-detail-lifecycle-badge` renders as a
 * `fleet-kit-badge` text variant whose computed colour is the lifecycle tone's own
 * (red for retired, cyan for enabled), and that it sits right-aligned in the panel's
 * header bar as the head of the trailing cluster. jsdom sees the badge's inline
 * `var(...)` string but never resolves it against the tokens, and never lays out the
 * `.p-hdr` flex row `graph-detail-header.css`'s `margin-left: auto` pushes against.
 *
 * Mounted at 800×600, `graph-detail.shell-sweep.spec.ts`'s own width. Graph detail is
 * not reachable from the hub's mobile bottom tab bar, so
 * `bzh:narrow-viewport-tier-rule` does not bind here.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
describe('graph detail header shell sweep (web:shell-sweep)', () => {
  for (const { retired, label, token } of [
    { retired: true, label: 'RETIRED', token: '--red' },
    { retired: false, label: 'ENABLED', token: '--cyan' },
  ]) {
    it(`renders a ${label.toLowerCase()} graph's lifecycle badge as a ${token} text badge, right-aligned`, async () => {
      const tokens = await loadDesignTokens();
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [SweepHost],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(SweepHost);
      fixture.componentInstance.retired.set(retired);
      const root = fixture.nativeElement as HTMLElement;
      root.style.cssText = 'display: block; width: 100%;';
      document.body.appendChild(root);
      await page.viewport(800, 600);
      await fixture.whenStable();

      try {
        const host = root.querySelector<HTMLElement>('[data-testid="graph-detail-lifecycle-badge"]')!;
        expect(host.tagName.toLowerCase()).toBe('fleet-kit-badge');
        const badge = host.querySelector<HTMLElement>('.badge')!;
        expect(getComputedStyle(badge).textTransform).toBe('uppercase');
        expect(badge.innerText.trim()).toBe(label);

        // The text variant: no pill/soft class, and no border painted.
        expect(badge.classList.contains('pill')).toBe(false);
        expect(badge.classList.contains('soft')).toBe(false);
        expect(getComputedStyle(badge).borderTopStyle).toBe('none');

        // The lifecycle tone's colour, genuinely resolved through the design tokens.
        const expected = resolvedToken(token);
        expect(expected).not.toBe(resolvedToken('--label-dim'));
        expect(getComputedStyle(badge).color).toBe(expected);

        // Right-aligned: the badge leads the header bar's trailing cluster — it sits
        // after the graph id, and only the retire/re-enable control follows it, flush
        // against the bar's own right edge.
        const bar = root.querySelector<HTMLElement>('.p-hdr')!;
        const barRect = bar.getBoundingClientRect();
        const gid = root.querySelector<HTMLElement>('[data-testid="graph-detail-graph-id"]')!;
        const control = root.querySelector<HTMLElement>(
          `[data-testid="${retired ? 'graph-detail-enable' : 'graph-detail-retire'}"]`,
        )!;
        const badgeRect = badge.getBoundingClientRect();
        const controlRect = control.getBoundingClientRect();
        const paddingRight = parseFloat(getComputedStyle(bar).paddingRight);
        expect(badgeRect.left).toBeGreaterThan(gid.getBoundingClientRect().right + 50);
        expect(controlRect.left).toBeGreaterThanOrEqual(badgeRect.right);
        expect(controlRect.left - badgeRect.right).toBeLessThanOrEqual(parseFloat(getComputedStyle(bar).columnGap) + 1);
        expect(Math.abs(barRect.right - paddingRight - controlRect.right)).toBeLessThanOrEqual(1);
      } finally {
        root.remove();
        tokens.remove();
      }
    });
  }
});

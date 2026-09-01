import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page, userEvent } from 'vitest/browser';

import { KitDialog } from './kit-dialog';

/**
 * `KitDialog`'s own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep, blizzard#392 D6)
 * — the modal shell's three claims jsdom cannot evaluate: the scrim genuinely covers
 * the full viewport (a real `getBoundingClientRect` against `window.inner*`), the
 * panel centres itself and its own `.body` scrolls a tall projection while the page
 * behind it does not, and `CdkTrapFocus` — a real focus-management directive, not a
 * plain DOM assertion — keeps repeated real `Tab` presses cycling inside the panel
 * rather than escaping to the page.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
@Component({
  imports: [KitDialog],
  template: `
    <div style="height: 3000px;">tall page content, above the dialog</div>
    <fleet-kit-dialog [open]="true" ariaLabel="Sweep dialog" testid="sweep-dialog" (closed)="closes += 1">
      <a fleetKitDialogHeader href="javascript:void(0)" data-testid="sweep-header-link">Header link</a>
      <div style="height: 2000px;" data-testid="sweep-tall-body">
        <input data-testid="sweep-body-input" placeholder="body field" />
      </div>
      <button fleetKitDialogFooter type="button" data-testid="sweep-footer-button">Footer button</button>
    </fleet-kit-dialog>
  `,
})
class SweepHost {
  closes = 0;
}

describe('KitDialog shell sweep (web:shell-sweep, blizzard#392 D6)', () => {
  it('covers the viewport, centres and self-scrolls the panel, and keeps focus inside', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await page.viewport(1024, 768);
    await TestBed.configureTestingModule({
      imports: [SweepHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SweepHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    await new Promise((resolve) => requestAnimationFrame(resolve));

    try {
      // The scrim covers the full viewport — not merely the dialog's own content box.
      const scrim = root.querySelector<HTMLElement>('.scrim')!;
      const scrimRect = scrim.getBoundingClientRect();
      expect(scrimRect.left, 'scrim left edge').toBeLessThanOrEqual(0);
      expect(scrimRect.top, 'scrim top edge').toBeLessThanOrEqual(0);
      expect(scrimRect.right, 'scrim right edge').toBeGreaterThanOrEqual(window.innerWidth);
      expect(scrimRect.bottom, 'scrim bottom edge').toBeGreaterThanOrEqual(window.innerHeight);

      // The panel is centred: near-equal gaps on either side of the viewport.
      const panel = root.querySelector<HTMLElement>('[data-testid="sweep-dialog"]')!;
      const panelRect = panel.getBoundingClientRect();
      const leftGap = panelRect.left;
      const rightGap = window.innerWidth - panelRect.right;
      expect(Math.abs(leftGap - rightGap), `left gap ${leftGap} vs right gap ${rightGap}`).toBeLessThan(2);

      // The panel's own body scrolls a tall projection; the page behind it does not.
      const body = root.querySelector<HTMLElement>('.body')!;
      expect(body.scrollHeight, 'body content must actually overflow to prove the scroll claim').toBeGreaterThan(
        body.clientHeight,
      );
      body.scrollTop = 500;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect(body.scrollTop, "the panel's own body must genuinely scroll").toBeGreaterThan(0);
      expect(document.scrollingElement?.scrollTop ?? 0, 'the page behind the dialog must not scroll').toBe(0);

      // Focus containment: CdkTrapFocusAutoCapture moves focus into the panel on open,
      // and repeated real Tab presses never let it escape to the page's own content.
      expect(panel.contains(document.activeElement), 'focus must land inside the panel on open').toBe(true);
      for (let i = 0; i < 8; i += 1) {
        await userEvent.tab();
        expect(panel.contains(document.activeElement), `tab ${i}: focus escaped the panel to ${document.activeElement?.outerHTML}`).toBe(
          true,
        );
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

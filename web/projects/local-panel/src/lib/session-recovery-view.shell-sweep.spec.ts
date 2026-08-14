import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { SessionRecoveryView } from './session-recovery-view';

/** Every page-error/unhandled-rejection listener a case in this sweep needs, shared so
 * both widths apply the same rigor rather than one asserting it and the other not. */
function trackPageErrors() {
  const errors: string[] = [];
  const onError = (e: ErrorEvent) => errors.push(e.message);
  const onRejection = (e: PromiseRejectionEvent) => errors.push(String(e.reason));
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  return {
    errors,
    stop: () => {
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    },
  };
}

// 390 (a typical phone) and 320 (the narrowest common phone) — this view replaces
// the whole panel, reachable at every width the shell itself is (blizzard#312).
const WIDTHS = [390, 320];

describe('session-recovery view shell sweep (web:shell-sweep, blizzard#312)', () => {
  for (const width of WIDTHS) {
    it(`renders the headline, detail, and retry control with no horizontal overflow at width ${width}`, async () => {
      const { errors: pageErrors, stop: stopTrackingPageErrors } = trackPageErrors();

      await TestBed.configureTestingModule({
        imports: [SessionRecoveryView],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(SessionRecoveryView);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 600);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const recovery = root.querySelector<HTMLElement>('[data-testid="session-recovery"]');
        expect(recovery, `width ${width}: no recovery surface in the DOM`).not.toBeNull();
        expect(
          recovery!.scrollWidth,
          `width ${width}: recovery surface overflows horizontally (${recovery!.scrollWidth} > ${recovery!.clientWidth})`,
        ).toBeLessThanOrEqual(recovery!.clientWidth);

        const retry = root.querySelector<HTMLElement>('[data-testid="session-recovery-retry"]');
        expect(retry, `width ${width}: no retry control in the DOM`).not.toBeNull();

        expect(
          root.scrollWidth,
          `width ${width}: recovery view overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        root.remove();
        stopTrackingPageErrors();
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });
  }
});

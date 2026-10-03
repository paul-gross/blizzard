import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { GraphDetailLifecycle } from './graph-detail-lifecycle';

/**
 * The Phase-2 `graph-detail` split's own render evidence (`bzh:visual-change-needs-a-
 * render`) — a real-Chromium geometry proof jsdom cannot make, for the one layout
 * decision the split actually changed: before it, the identity row, lifecycle
 * actions, action-error line, and entry line were direct children of `.body`'s own
 * `flex-direction: column; gap: 10px` (`graph-detail.css`); after it (and after the
 * identity row — and, later, the retire/re-enable control itself — moved into
 * `fleet-kit-panel`'s own header bar, `graph-detail-header.ts`),
 * `GraphDetailLifecycle` holds the two remaining blocks in a single flex slot, so
 * `graph-detail-lifecycle.css`'s `:host` has to reproduce that same column/gap
 * internally or they would render pressed flush together with no gap at all — a
 * regression jsdom's unevaluated `@` rules and non-laid-out flexbox can't see
 * (`web:unit-test` only asserts *which* blocks render, never their real position).
 *
 * Mounts the component directly with plain inputs — no query double, matching how it
 * is actually used (`bzh:frontend-container-presentational`; `GraphDetail` forwards
 * resolved data into it). `GraphDetailHeader` (the identity supplement,
 * plus the retire/re-enable control it now also carries) has no internal layout of
 * its own to sweep: `:host { display: contents }` leaves every span and button a
 * direct flex item of `fleet-kit-panel`'s own `.p-hdr` row, so its geometry —
 * including the `margin-left: auto` that right-aligns its trailing cluster — is
 * `KitPanel`'s own flex row, not this split's concern.
 *
 * Proven able to fail by setting `graph-detail-lifecycle.css`'s `:host` `gap` to `0`:
 * the two blocks then sit within a few pixels of each other instead of the ~10px
 * this spec pins.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
async function mountLifecycle(): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GraphDetailLifecycle],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphDetailLifecycle);
  fixture.componentRef.setInput('actionError', 'Retire failed.');
  fixture.componentRef.setInput('entryNodeName', 'build');
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(800, 600);
  return root;
}

describe('graph-detail-lifecycle shell sweep (web:shell-sweep)', () => {
  it("keeps GraphDetailLifecycle's error line and entry line genuinely stacked with a real gap", async () => {
    const root = await mountLifecycle();
    try {
      const error = root.querySelector<HTMLElement>('[data-testid="graph-detail-lifecycle-error"]')!;
      const entry = root.querySelector<HTMLElement>('[data-testid="graph-detail-entry"]')!;

      const errorRect = error.getBoundingClientRect();
      const entryRect = entry.getBoundingClientRect();

      // Genuinely a column: the entry line sits below the error line, not overlapping.
      expect(entryRect.top, 'the entry line did not land below the error line').toBeGreaterThanOrEqual(
        errorRect.bottom,
      );

      // A real gap survives the move — not the flush-together layout a `:host` with no
      // `gap` (or `display: block`'s default) would produce.
      expect(
        entryRect.top - errorRect.bottom,
        `the gap between the error line and the entry line (${entryRect.top - errorRect.bottom}px) collapsed`,
      ).toBeGreaterThan(4);
    } finally {
      root.remove();
    }
  });
});

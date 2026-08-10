import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { TranscriptPanel } from './transcript-panel';

const TRANSCRIPT_ROUTE = '/api/leases/L-903/transcript';

/**
 * A closed lease's archived read (blizzard#249): the archived badge stacked above the
 * truncation banner, the widest pairing this change adds to the populated state — and a
 * narrow width is the only tier that can judge it, since the transcript panel is reachable
 * from the mobile chunk-detail screen (`data-testid="detail-transcript"`). No turns beyond
 * the two banners: turn-row wrapping belongs to the shared `TranscriptViewer` (blizzard#248),
 * not to this change, and this sweep does not exist to re-prove it.
 */
const ARCHIVED_TRANSCRIPT = {
  lease_id: 'L-903',
  session_id: 'sess-77',
  available: true,
  reason: null,
  truncated: true,
  provenance: 'archived',
  hub_unreachable: false,
  turns: [],
};

/** The hub-unreachable-with-no-answer state (D1) — its banner is the widest single row
 * this change adds, so it is the one worth proving does not overflow. */
const HUB_UNREACHABLE_TRANSCRIPT = {
  lease_id: 'L-903',
  session_id: 'sess-77',
  available: false,
  reason: 'not_found',
  truncated: false,
  provenance: 'local',
  hub_unreachable: true,
  turns: [],
};

/** Every page-error/unhandled-rejection listener a case in this sweep needs, shared so
 * both cases apply the same rigor rather than one asserting it and the other not. */
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

async function render(body: unknown) {
  const stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === TRANSCRIPT_ROUTE ? body : {}));
  await TestBed.configureTestingModule({
    imports: [TranscriptPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(TranscriptPanel);
  fixture.componentRef.setInput('leaseId', 'L-903');
  await fixture.whenStable();
  return { fixture, stub };
}

// 390 (a typical phone) and 320 (the narrowest common phone) — the widths this
// panel is actually reached at, inside the mobile chunk-detail screen beneath
// the persistent mobile bottom tab bar.
const WIDTHS = [390, 320];

describe('transcript panel shell sweep (web:shell-sweep, blizzard#249)', () => {
  for (const width of WIDTHS) {
    it(`renders the archived badge and truncation banner with no horizontal overflow at width ${width}`, async () => {
      const { errors: pageErrors, stop: stopTrackingPageErrors } = trackPageErrors();

      const { fixture, stub } = await render(ARCHIVED_TRANSCRIPT);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 600);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const archived = root.querySelector<HTMLElement>('[data-testid="transcript-archived-badge"]');
        expect(archived, `width ${width}: no archived badge in the DOM`).not.toBeNull();
        expect(
          archived!.scrollWidth,
          `width ${width}: archived badge overflows horizontally (${archived!.scrollWidth} > ${archived!.clientWidth})`,
        ).toBeLessThanOrEqual(archived!.clientWidth);

        const truncated = root.querySelector<HTMLElement>('[data-testid="transcript-truncated"]');
        expect(truncated, `width ${width}: no truncation banner in the DOM`).not.toBeNull();
        expect(
          truncated!.scrollWidth,
          `width ${width}: truncation banner overflows horizontally (${truncated!.scrollWidth} > ${truncated!.clientWidth})`,
        ).toBeLessThanOrEqual(truncated!.clientWidth);

        expect(
          root.scrollWidth,
          `width ${width}: transcript panel overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        root.remove();
        stub.restore();
        stopTrackingPageErrors();
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });

    it(`renders the hub-unreachable banner with no horizontal overflow at width ${width}`, async () => {
      const { errors: pageErrors, stop: stopTrackingPageErrors } = trackPageErrors();

      const { fixture, stub } = await render(HUB_UNREACHABLE_TRANSCRIPT);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 600);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const banner = root.querySelector<HTMLElement>('[data-testid="transcript-hub-unreachable"]');
        expect(banner, `width ${width}: no hub-unreachable banner in the DOM`).not.toBeNull();

        expect(
          root.scrollWidth,
          `width ${width}: transcript panel overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        root.remove();
        stub.restore();
        stopTrackingPageErrors();
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });
  }
});

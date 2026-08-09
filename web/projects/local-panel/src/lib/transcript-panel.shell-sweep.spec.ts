import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { TranscriptPanel } from './transcript-panel';

const TRANSCRIPT_ROUTE = '/api/leases/L-903/transcript';

/**
 * A closed lease's archived read (blizzard#249): the archived badge and a
 * dropped-turns count both in view — the combination narrow width is the
 * only tier that can judge, since the transcript panel is reachable from the
 * mobile chunk-detail screen (`data-testid="detail-transcript"`,
 * `local-panel-mobile.spec.ts:171`). No turns beyond the two banners: turn
 * row wrapping is the pre-existing (unchanged by this phase) `.tc-name`
 * rendering this sweep does not exist to re-prove.
 */
const ARCHIVED_TRANSCRIPT = {
  lease_id: 'L-903',
  session_id: 'sess-77',
  available: true,
  reason: null,
  truncated: false,
  provenance: 'archived',
  hub_unreachable: false,
  dropped_turns: 7,
  turns: [],
};

/** The hub-unreachable-with-no-answer state (D1) — its banner is the widest
 * of the three new rows, so it is the one worth proving does not overflow. */
const HUB_UNREACHABLE_TRANSCRIPT = {
  lease_id: 'L-903',
  session_id: 'sess-77',
  available: false,
  reason: 'not_found',
  truncated: false,
  provenance: 'local',
  hub_unreachable: true,
  dropped_turns: 0,
  turns: [],
};

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
    it(`renders the archived badge and dropped-turns count with no horizontal overflow at width ${width}`, async () => {
      const pageErrors: string[] = [];
      const onError = (e: ErrorEvent) => pageErrors.push(e.message);
      const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
      window.addEventListener('error', onError);
      window.addEventListener('unhandledrejection', onRejection);

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

        const dropped = root.querySelector<HTMLElement>('[data-testid="transcript-dropped-turns"]');
        expect(dropped, `width ${width}: no dropped-turns note in the DOM`).not.toBeNull();
        expect(
          dropped!.scrollWidth,
          `width ${width}: dropped-turns note overflows horizontally (${dropped!.scrollWidth} > ${dropped!.clientWidth})`,
        ).toBeLessThanOrEqual(dropped!.clientWidth);

        expect(
          root.scrollWidth,
          `width ${width}: transcript panel overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        root.remove();
        stub.restore();
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onRejection);
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });

    it(`renders the hub-unreachable banner with no horizontal overflow at width ${width}`, async () => {
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
      }
    });
  }
});

import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { EventsPage } from './events-page';

/**
 * The Events page's feed scrolls inside its panel: with more events than the
 * viewport holds, `.rows` — and only `.rows` — overflows its box and scrolls to
 * the last event. jsdom never lays out, so the host-chain `min-height: 0` /
 * `display: flex` this relies on is only provable in a real browser
 * (`bzh:visual-change-needs-a-render`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`.
 */
const EVENTS = Array.from({ length: 80 }, (_, i) => ({
  id: 80 - i,
  recorded_at: '2026-07-16T00:00:00Z',
  severity: 'info',
  kind: 'lease-minted',
  runner_id: 'rn_01',
  message: `Event ${80 - i}`,
}));

/** Stands in for the app shell's bounded, clipping `.shell` around the routed page. */
@Component({
  selector: 'app-test-events-shell',
  imports: [EventsPage],
  template: '<app-events-page />',
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      height: 600px;
      overflow: hidden;
    }
    app-events-page {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
    }
  `,
})
class TestEventsShell {}

const scrolls = (el: Element) => getComputedStyle(el).overflowY === 'auto' && el.scrollHeight > el.clientHeight;

describe('events feed scroll shell sweep (web:shell-sweep)', () => {
  for (const width of [1280, 390]) {
    it(`scrolls the rows list, and only it, to the last event at ${width}px`, async () => {
      const stub = stubRequestClient(hubClient, (method, path) => (method === 'GET' && path === '/api/events' ? { events: EVENTS } : {}));
      await TestBed.configureTestingModule({
        imports: [TestEventsShell],
        providers: [
          provideZonelessChangeDetection(),
          provideRouter([]),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        ],
      }).compileComponents();
      await page.viewport(width, 800);
      const fixture = TestBed.createComponent(TestEventsShell);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await settle(fixture, 12);

      const rows = root.querySelector<HTMLElement>('[data-testid="events-rows"]')!;
      const body = rows.closest('.p-body')!;
      expect(rows.querySelectorAll('[data-testid="events-row"]')).toHaveLength(80);
      expect(scrolls(rows)).toBe(true);
      expect(scrolls(body)).toBe(false);

      rows.scrollTop = rows.scrollHeight;
      expect(rows.scrollTop).toBeGreaterThan(0);
      const last = rows.querySelectorAll('[data-testid="events-row"]')[79].getBoundingClientRect();
      expect(last.bottom).toBeLessThanOrEqual(rows.getBoundingClientRect().bottom + 1);

      root.remove();
      stub.restore();
    });
  }
});

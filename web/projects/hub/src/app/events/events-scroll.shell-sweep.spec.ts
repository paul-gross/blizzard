import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, RouterOutlet, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { eventView, settle, stubHubClient } from 'fleet/testing';
import { APP_SCOPE, type InAppLink } from 'fleet/shell';
import { page } from 'vitest/browser';

import { EventsPage } from './events-page';
import { EventDetailPage } from './event-detail-page';
import { eventFingerprint, findEvent } from './event-detail.model';
import { HUB_PROJECT_FILTER } from '../core/project-filter';

/**
 * The Events page's feed scrolls inside its panel: with more events than the
 * viewport holds, `.rows` — and only `.rows` — overflows its box and scrolls to
 * the last event. jsdom never lays out, so the host-chain `min-height: 0` /
 * `display: flex` this relies on is only provable in a real browser
 * (`bzh:visual-change-needs-a-render`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`.
 */
const EVENTS = Array.from({ length: 80 }, (_, i) =>
  eventView({
    id: 80 - i,
    recorded_at: '2026-07-16T00:00:00Z',
    severity: 'info',
    kind: 'lease-minted',
    runner_id: 'rn_01',
    message: `Event ${80 - i}`,
  }),
);

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

@Component({ imports: [RouterOutlet], template: '<router-outlet />' })
class RoutedEventsShell {}

const scrolls = (el: Element) => getComputedStyle(el).overflowY === 'auto' && el.scrollHeight > el.clientHeight;

describe('events feed scroll shell sweep (web:shell-sweep)', () => {
  it('opens a filtered projected event by its native fingerprint across feed shifts and refuses replacements', async () => {
    const original = eventView({
      id: -1, recorded_at: '2026-07-16T00:00:00Z', kind: 'needs-human', severity: 'critical',
      runner_id: 'rn_01', chunk_id: 'ch_01', message: 'Original escalation', detail: { a: 1, b: 2 },
    });
    let events = [original];
    const stub = stubHubClient({ 'GET /api/events': () => ({ events }) });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const lensPath = (link: InAppLink): InAppLink =>
      typeof link === 'string' ? `/p/blizzard${link}` : ['/p/blizzard', ...link.map((part, i) =>
        i === 0 && typeof part === 'string' ? part.replace(/^\//, '') : part)];
    let root: HTMLElement | undefined;
    try {
      await TestBed.configureTestingModule({
        imports: [RoutedEventsShell],
        providers: [
          provideZonelessChangeDetection(),
          provideRouter([
            { path: 'p/blizzard/events', component: EventsPage },
            { path: 'p/blizzard/events/:eventId', component: EventDetailPage },
          ]),
          provideTanStackQuery(client),
          { provide: HUB_PROJECT_FILTER, useValue: signal('blizzard') },
          { provide: APP_SCOPE, useValue: { path: lensPath, query: () => ({}) } },
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(RoutedEventsShell);
      root = fixture.nativeElement as HTMLElement;
      const element = root;
      document.body.appendChild(root);
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/p/blizzard/events?severity=critical&runner=rn_01&chunk=ch_01');
      await pumpUntil(fixture, () => !!element.querySelector('[data-testid="events-open"]'));

      const link = root.querySelector<HTMLAnchorElement>('[data-testid="events-open"]')!;
      expect(link).not.toBeNull();
      const href = new URL(link.href);
      expect(href.pathname).toBe('/p/blizzard/events/-1');
      expect(href.searchParams.get('fingerprint')).toMatch(/^[a-f0-9]{64}$/);
      expect(stub.forRoute('/api/events', 'GET').some((request) => {
        const params = new URLSearchParams(request.search);
        return params.get('severity') === 'critical' && params.get('runner_id') === 'rn_01' &&
          params.get('chunk_id') === 'ch_01' && params.get('project') === 'blizzard';
      })).toBe(true);

      link.click();
      await pumpUntil(fixture, () => !!element.querySelector('[data-testid="event-detail"]'));
      expect(root.querySelector('[data-testid="event-detail"]')?.textContent).toContain('Original escalation');

      // A newly projected event shifts the synthetic id; retain the immutable payload.
      events = [{ ...original, id: -5, runner_name: 'Shifted runner', detail: { b: 2, a: 1 } }];
      await client.invalidateQueries({ queryKey: ['hub', 'events'] });
      await pumpUntil(fixture, () =>
        !!element.querySelector('[data-testid="event-detail-runner"]')?.textContent?.includes('Shifted runner'));
      expect(router.url).toContain('/events/-1');
      expect(root.querySelector('[data-testid="event-detail"]')?.textContent).toContain('Original escalation');

      events = [{ ...original, id: -5, message: 'Replacement escalation' }];
      await client.invalidateQueries({ queryKey: ['hub', 'events'] });
      await pumpUntil(fixture, () => !!element.querySelector('[data-testid="event-detail-unavailable"]'));
      expect(root.querySelector('[data-testid="event-detail"]')).toBeNull();
      expect(root.querySelector('[data-testid="event-detail-unavailable"]')).not.toBeNull();

      root.querySelector<HTMLAnchorElement>('[data-testid="event-detail-back"]')!.click();
      await pumpUntil(fixture, () => router.url.startsWith('/p/blizzard/events?') &&
        !!element.querySelector('app-events-page app-events-panel'));
      const back = router.parseUrl(router.url);
      expect(back.root.children['primary'].segments.map((segment) => segment.path)).toEqual(['p', 'blizzard', 'events']);
      expect(back.queryParams).toEqual({ severity: 'critical', runner: 'rn_01', chunk: 'ch_01' });
      expect(root.querySelector('app-events-page app-events-panel')).not.toBeNull();
    } finally {
      root?.remove();
      TestBed.resetTestingModule();
      client.clear();
      stub.restore();
    }
  });
  it('uses native SHA-256 to reject a replacement projected event', async () => {
    const original = eventView({ id: -1, kind: 'needs-human', recorded_at: '2026-07-16T00:00:00Z',
      chunk_id: 'ch_1', message: 'original', detail: { a: 1, b: 2 } });
    const fingerprint = await eventFingerprint(original);
    expect(fingerprint).toMatch(/^[a-f0-9]{64}$/);
    const resolve = (event: typeof original) =>
      findEvent([event], '-1', original.recorded_at, original.kind, original.chunk_id, fingerprint);
    expect(await resolve({ ...original, id: -5, detail: { b: 2, a: 1 } })).not.toBeNull();
    expect(await resolve({ ...original, id: -5, message: 'replacement' })).toBeNull();
    expect(await resolve({ ...original, id: -5, detail: { a: 2, b: 2 } })).toBeNull();
  });
  for (const width of [1280, 390]) {
    it(`scrolls the rows list, and only it, to the last event at ${width}px`, async () => {
      const stub = stubHubClient({ 'GET /api/events': () => ({ events: EVENTS }) });
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
      const body = rows.closest('.body')!;
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

/** Wait for observable routed content without zoneless whenStable's pending-query deadlock. */
async function pumpUntil(fixture: ComponentFixture<unknown>, ready: () => boolean): Promise<void> {
  for (let i = 0; i < 60; i += 1) {
    fixture.detectChanges();
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  fixture.detectChanges();
  if (!ready()) throw new Error('pumpUntil: the awaited content never rendered');
}

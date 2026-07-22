import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { vi } from 'vitest';

import { EventsPage } from './events-page';

/**
 * Exercises `EventsPage`'s composition of `fleet-events-panel` and its
 * `selectChunk` wiring. The hub client's transport is stubbed to answer every
 * read `404`, so `EventsPanel` settles into its own error state — enough to
 * prove the page mounts it and forwards its output, without duplicating
 * `EventsPanel`'s own read/filter coverage (`events-panel.spec.ts`).
 */
describe('EventsPage', () => {
  beforeEach(() => {
    hubClient.setConfig({
      baseUrl: 'http://localhost',
      fetch: (async () => new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })) as typeof fetch,
    });
  });

  afterEach(() => {
    hubClient.setConfig({ baseUrl: '', fetch: undefined });
  });

  async function mount() {
    const navigate = vi.fn();
    await TestBed.configureTestingModule({
      imports: [EventsPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: Router, useValue: { navigate } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(EventsPage);
    await fixture.whenStable();
    return { fixture, navigate };
  }

  it('mounts the events panel', async () => {
    const { fixture } = await mount();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-events-panel')).toBeTruthy();
  });

  it('navigates to /board when the panel emits a chunk selection', async () => {
    const { fixture, navigate } = await mount();

    const panel = fixture.debugElement.query(By.css('fleet-events-panel'));
    panel.componentInstance.selectChunk.emit('ch_live');
    await fixture.whenStable();

    expect(navigate).toHaveBeenCalledWith(['/board']);
  });
});

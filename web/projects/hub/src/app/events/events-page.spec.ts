import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { stubHubClient } from 'fleet/testing';
import { EventsPage } from './events-page';

describe('EventsPage', () => {
  it('mounts the events panel', async () => {
    const stub = stubHubClient({ 'GET /api/events': () => ({ events: [] }) });
    try {
      TestBed.configureTestingModule({
        imports: [EventsPage],
        providers: [provideZonelessChangeDetection(), provideRouter([]), provideTanStackQuery(new QueryClient())],
      });
      const fixture = TestBed.createComponent(EventsPage);
      await fixture.whenStable();
      expect((fixture.nativeElement as HTMLElement).querySelector('app-events-panel')).not.toBeNull();
    } finally {
      stub.restore();
    }
  });
});

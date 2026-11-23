import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { eventLogEntry } from './event-log.builders';
import { EventDetailView } from './event-detail-view';

describe('EventDetailView', () => {
  it.each([false, true])('links runner and chunk independently, mobile=%s', async (mobile) => {
    TestBed.configureTestingModule({
      imports: [EventDetailView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    });
    const fixture = TestBed.createComponent(EventDetailView);
    fixture.componentRef.setInput('event', eventLogEntry({ runnerId: 'rn_1', chunkId: 'ch_1' }));
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('mobile', mobile);
    fixture.componentRef.setInput('hosts', [{ host_id: 'hs_1', runner_ids: ['rn_1'] }]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const runner = root.querySelector<HTMLAnchorElement>('[data-testid="event-detail-runner"]')!;
    const chunk = root.querySelector<HTMLAnchorElement>('[data-testid="event-detail-chunk"]')!;
    expect(runner.getAttribute('href')).toBe('/fleet/hs_1');
    expect(chunk.getAttribute('href')).toBe(mobile ? '/board/chunk/ch_1' : '/board?chunk=ch_1');
    expect(runner.classList.contains('runner-link')).toBe(true);
    expect(chunk.classList.contains('chunk-link')).toBe(true);
  });
});

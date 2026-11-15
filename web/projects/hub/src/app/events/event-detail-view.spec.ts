import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { eventView } from 'fleet/testing';
import { EventDetailView } from './event-detail-view';

describe('EventDetailView', () => {
  it.each([false, true])('links runner and chunk independently, mobile=%s', async (mobile) => {
    TestBed.configureTestingModule({
      imports: [EventDetailView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    });
    const fixture = TestBed.createComponent(EventDetailView);
    fixture.componentRef.setInput('event', eventView({ runner_id: 'rn_1', chunk_id: 'ch_1' }));
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('mobile', mobile);
    fixture.componentRef.setInput('runnerFleetIds', new Map([['rn_1', 'hs_1']]));
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

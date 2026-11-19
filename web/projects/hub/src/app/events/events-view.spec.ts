import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { eventView } from 'fleet/testing';
import { EventsView } from './events-view';

describe('EventsView', () => {
  beforeEach(() => TestBed.configureTestingModule({
    imports: [EventsView],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }));

  async function render(state = 'ready', mobile = false) {
    const fixture = TestBed.createComponent(EventsView);
    fixture.componentRef.setInput('events', [eventView({
      id: 12, kind: 'lease-minted', severity: 'info', recorded_at: '2026-07-16T00:00:00Z',
      runner_id: 'rn_01', chunk_id: 'ch_1234', message: 'Lease minted',
    })]);
    fixture.componentRef.setInput('now', Date.parse('2026-07-16T00:01:00Z'));
    fixture.componentRef.setInput('state', state);
    fixture.componentRef.setInput('mobile', mobile);
    fixture.componentRef.setInput('hosts', [{ host_id: 'host_01', runner_ids: ['rn_01'] }]);
    await fixture.whenStable();
    return fixture;
  }

  it('renders three standard filters and compact cards with separate routed links', async () => {
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelectorAll('fleet-kit-select')).toHaveLength(3);
    expect(root.querySelector('[data-testid="events-message"]')?.textContent).toContain('Lease minted');
    expect(root.querySelector('[data-testid="events-open"]')?.getAttribute('href')).toContain('/events/12');
    expect(root.querySelector('[data-testid="events-runner"]')?.getAttribute('href')).toContain('/fleet/host_01');
    expect(root.querySelector('[data-testid="events-chunk"]')?.getAttribute('href')).toContain('/board?chunk=ch_1234');
  });

  it('links chunks to the mobile detail route', async () => {
    const fixture = await render('ready', true);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="events-chunk"]')?.getAttribute('href'))
      .toContain('/board/chunk/ch_1234');
  });

  it.each(['loading', 'error', 'empty'])('renders the %s state', async (state) => {
    const fixture = await render(state);
    expect((fixture.nativeElement as HTMLElement).querySelector(`[data-testid="events-${state}"]`)).not.toBeNull();
  });
});

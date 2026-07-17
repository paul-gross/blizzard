import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, type EventSourceFactory } from 'fleet';

import { App } from './app';

/** A do-nothing EventSource so the live-update spine can open without a real stream. */
class FakeEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener(): void {
    /* no-op: the test never drives the stream */
  }
  close(): void {
    /* no-op */
  }
}

describe('hub App', () => {
  beforeEach(async () => {
    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as EventSource;
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
  });

  it('renders the shared fleet board shell and the operator controls', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-board-shell')).toBeTruthy();
    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    // The titlebar spans the window above the columns, and the two rails compose
    // alongside the board: queue + event log at the left, runners + asks at the right.
    expect(el.querySelector('[data-testid="board-header"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="queue-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="event-log-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="runner-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="questions-panel"]')).toBeTruthy();
  });

  it('lays the board out as three columns under the titlebar', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The titlebar is a sibling of the columns, not nested in one of them — a header
    // inside the board column would leave the rails starting above it.
    const header = el.querySelector('fleet-board-header');
    expect(header?.parentElement?.classList.contains('layout')).toBe(true);
    expect(header?.closest('.main')).toBeNull();

    // Each rail and the centre are their own full-height column of the main grid, so
    // the rails run titlebar → bottom rather than stopping at the detail dock.
    const rails = el.querySelectorAll('.main > .col');
    expect(rails.length).toBe(3);
    expect(el.querySelector('[data-testid="queue-panel"]')?.closest('.col')).toBe(rails[0]);
    expect(el.querySelector('fleet-board-shell')?.closest('.col')).toBe(rails[1]);
    expect(el.querySelector('[data-testid="runner-panel"]')?.closest('.col')).toBe(rails[2]);
  });

  it('docks chunk detail beside the rails, so selecting never resizes the board (issue #21)', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Nothing selected: the dock is already mounted, stacked under the board inside
    // the centre column — never spanning the window beneath the rails — and holds a
    // rest state prompting the operator to pick a chunk.
    const dockBefore = el.querySelector('fleet-chunk-detail.dock');
    expect(dockBefore).toBeTruthy();
    expect(dockBefore?.closest('.col')).toBe(el.querySelector('fleet-board-shell')?.closest('.col'));
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent).toContain('SELECT');

    // Selecting a card fills the SAME dock element — the layout gains no node, so the
    // board columns cannot resize or shift.
    fixture.debugElement.query(By.css('fleet-board-shell')).componentInstance.selectChunk.emit('ch_1');
    await fixture.whenStable();

    const dockAfter = el.querySelector('fleet-chunk-detail.dock');
    expect(dockAfter).toBe(dockBefore);
    expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent ?? '').not.toContain('SELECT');
  });

  it('opens a chunk from an ask in the right rail (MVP criterion 7)', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // An ask names a chunk nobody has selected; activating it fills the same dock the
    // board cards fill, which is where the answer is given.
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    fixture.debugElement.query(By.css('fleet-questions-panel')).componentInstance.selectChunk.emit('ch_asked');
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent ?? '').not.toContain('SELECT');
  });
});

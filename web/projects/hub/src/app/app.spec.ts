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
    // The queue-shaping panel and the runner strip compose alongside the board.
    expect(el.querySelector('[data-testid="queue-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="runner-strip"]')).toBeTruthy();
  });

  it('fills a bottom dock beside the workspace — not a workspace column — when a chunk is selected (issue #21)', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Nothing selected: the dock is absent and the board+rail workspace stands alone.
    expect(el.querySelector('fleet-chunk-detail')).toBeNull();
    expect(el.querySelector('.workspace')).toBeTruthy();

    // Selecting a card fills the dock.
    fixture.debugElement.query(By.css('fleet-board-shell')).componentInstance.selectChunk.emit('ch_1');
    await fixture.whenStable();

    const dock = el.querySelector('fleet-chunk-detail');
    expect(dock).toBeTruthy();
    // The dock is a layout-level row, a sibling of the workspace — never a third
    // column inside it — so filling it cannot reflow the board columns.
    expect(dock?.classList.contains('dock')).toBe(true);
    expect(dock?.closest('.workspace')).toBeNull();
    expect(dock?.parentElement?.classList.contains('layout')).toBe(true);
  });
});

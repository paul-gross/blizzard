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

  it('docks chunk detail in a persistent bottom row, so selecting never resizes the board (issue #21)', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Nothing selected: the dock is already mounted as a layout-level row — a
    // sibling of the workspace, never a third column inside it — and holds a rest
    // state prompting the operator to pick a chunk.
    const dockBefore = el.querySelector('fleet-chunk-detail.dock');
    expect(dockBefore).toBeTruthy();
    expect(dockBefore?.closest('.workspace')).toBeNull();
    expect(dockBefore?.parentElement?.classList.contains('layout')).toBe(true);
    expect(el.querySelector('.workspace')).toBeTruthy();
    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent).toContain('SELECT');

    // Selecting a card fills the SAME dock element — the board layout gains no node,
    // so the board columns cannot resize or shift.
    fixture.debugElement.query(By.css('fleet-board-shell')).componentInstance.selectChunk.emit('ch_1');
    await fixture.whenStable();

    const dockAfter = el.querySelector('fleet-chunk-detail.dock');
    expect(dockAfter).toBe(dockBefore);
    // The rest prompt is gone (the dock now reflects the selected chunk), while the
    // dock stays a layout row beneath — not inside — the workspace.
    expect(el.querySelector('[data-testid="chunk-detail-empty"]')?.textContent ?? '').not.toContain('SELECT');
    expect(dockAfter?.closest('.workspace')).toBeNull();
  });
});

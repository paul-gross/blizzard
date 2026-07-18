import { type WritableSignal, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetLiveUpdates, type LoggedEvent } from '../sse/fleet-live';
import { EventLogPanel } from './event-log-panel';

describe('EventLogPanel', () => {
  let log: WritableSignal<readonly LoggedEvent[]>;

  beforeEach(async () => {
    log = signal<readonly LoggedEvent[]>([]);
    // A stub live-update spine exposing just the feed the panel reads.
    const fakeLive = { log: () => log() } as unknown as FleetLiveUpdates;
    await TestBed.configureTestingModule({
      imports: [EventLogPanel],
      providers: [provideZonelessChangeDetection(), { provide: FleetLiveUpdates, useValue: fakeLive }],
    }).compileComponents();
  });

  it('shows an empty state and a zero count before any event', () => {
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="event-log-count"]')?.textContent).toContain('0 ev');
  });

  it('renders newest-first rows with human-readable summaries and a matching count', () => {
    log.set([
      { seq: 1, type: 'chunk-changed', data: { chunk_id: 'ch_alpha', status: 'running' }, at: 0 },
      { seq: 2, type: 'question-asked', data: { chunk_id: 'ch_beta', question_id: 'q1' }, at: 0 },
    ]);
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="event-log-row"]');
    expect(rows).toHaveLength(2);

    const messages = [...el.querySelectorAll('[data-testid="event-log-message"]')].map((n) => n.textContent?.trim());
    // Newest first: the question-asked (seq 2) renders above the chunk-changed (seq 1).
    expect(messages[0]).toContain('asked a question');
    // The chunk id renders through compactRef (issue #81), not the raw id.
    expect(messages[0]).toContain('C-beta');
    expect(messages[1]).toContain('running');
    expect(el.querySelector('[data-testid="event-log-count"]')?.textContent).toContain('2 ev');
  });
});

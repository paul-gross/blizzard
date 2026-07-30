import { type WritableSignal, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetLiveUpdates, type LoggedEvent } from '../sse/fleet-live';
import type { SseStatus } from '../sse/sse.service';
import { EventLogPanel } from './event-log-panel';

describe('EventLogPanel', () => {
  let log: WritableSignal<readonly LoggedEvent[]>;
  let status: WritableSignal<SseStatus>;
  let authFailed: WritableSignal<boolean>;

  beforeEach(async () => {
    log = signal<readonly LoggedEvent[]>([]);
    status = signal<SseStatus>('open');
    authFailed = signal(false);
    // A stub live-update spine exposing just what the panel reads.
    const fakeLive = {
      log: () => log(),
      status: () => status(),
      authFailed: () => authFailed(),
    } as unknown as FleetLiveUpdates;
    await TestBed.configureTestingModule({
      imports: [EventLogPanel],
      providers: [provideZonelessChangeDetection(), { provide: FleetLiveUpdates, useValue: fakeLive }],
    }).compileComponents();
  });

  it('shows an empty state before any event, once connected', () => {
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-empty"]')).toBeTruthy();
    // The panel header carries no running event count (issue #139).
    expect(el.querySelector('[data-testid="event-log-count"]')).toBeNull();
  });

  it('withholds the empty copy while the stream has never yet connected (AC 3)', () => {
    status.set('idle');
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="event-log-empty"]')).toBeNull();
  });

  it('shows an error state on a terminal auth failure', () => {
    status.set('closed');
    authFailed.set(true);
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-error"]')).toBeTruthy();
  });

  it('does not regress an already-rendered feed to loading on a reconnect (AC 6)', () => {
    log.set([{ seq: 1, type: 'chunk-changed', data: { chunk_id: 'ch_alpha', status: 'running' }, at: 0 }]);
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();

    status.set('reconnecting');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="event-log-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="event-log-loading"]')).toBeNull();
  });

  it('renders newest-first rows with human-readable summaries', () => {
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
  });

  it('renders a runner-changed frame as what actually changed', () => {
    // Issue #151: the pause family is the only kind that reaches the feed (fleet-live
    // mutes registration/heartbeat), and each row must say who braked the runner and why.
    log.set([
      { seq: 1, type: 'runner-changed', data: { runner_id: 'runner-local', kind: 'paused', by: 'operator' }, at: 0 },
      {
        seq: 2,
        type: 'runner-changed',
        data: { runner_id: 'runner-local', kind: 'locally-paused', by: 'runner-ceiling', reason: 'disk full' },
        at: 0,
      },
      { seq: 3, type: 'runner-changed', data: { runner_id: 'runner-local', kind: 'locally-resumed', by: 'ada' }, at: 0 },
    ]);
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    // Newest first, so seq 3 leads.
    const messages = [...el.querySelectorAll('[data-testid="event-log-message"]')].map((n) => n.textContent?.trim());
    expect(messages).toEqual([
      'runner runner-local locally resumed by ada',
      'runner runner-local locally paused by runner-ceiling — disk full',
      'runner runner-local paused by operator',
    ]);
  });

  it('degrades a kind-less runner-changed frame rather than rendering it blank', () => {
    log.set([{ seq: 1, type: 'runner-changed', data: { runner_id: 'runner-local' }, at: 0 }]);
    const fixture = TestBed.createComponent(EventLogPanel);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="event-log-message"]')?.textContent?.trim()).toBe(
      'runner runner-local changed',
    );
  });
});

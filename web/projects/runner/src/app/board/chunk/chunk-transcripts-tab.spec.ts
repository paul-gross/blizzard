import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';

import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

const LEASE_A = { value: 'lease_a', label: 'a1 running', testid: 'attempt-tab' };
const LEASE_B = { value: 'lease_b', label: 'a2 running', testid: 'attempt-tab' };

describe('ChunkTranscriptsTab', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [ChunkTranscriptsTab],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
  });

  it('renders the attempts loading state with no chips or transcript panel', async () => {
    const fixture = TestBed.createComponent(ChunkTranscriptsTab);
    fixture.componentRef.setInput('attemptsState', 'loading');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="attempts-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="attempt-tabs"]')).toBeNull();
    expect(el.querySelector('local-transcript-panel')).toBeNull();
  });

  it('renders the attempts empty state for a chunk with no recorded leases', async () => {
    const fixture = TestBed.createComponent(ChunkTranscriptsTab);
    fixture.componentRef.setInput('attemptsState', 'empty');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="attempts-empty"]')?.textContent).toContain('NO RECENT ATTEMPTS');
  });

  it('hides the chip picker with a single attempt but still mounts the transcript panel', async () => {
    const stub = stubRequestClient(runnerClient, () => ({
      lease_id: 'lease_a',
      session_id: 'sess',
      available: true,
      reason: null,
      truncated: false,
      turns: [],
    }));
    const fixture = TestBed.createComponent(ChunkTranscriptsTab);
    fixture.componentRef.setInput('attemptsState', 'ready');
    fixture.componentRef.setInput('attemptOptions', [LEASE_A]);
    fixture.componentRef.setInput('activeAttemptLeaseId', 'lease_a');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="attempt-tabs"]')).toBeNull();
    expect(el.querySelector('local-transcript-panel')).not.toBeNull();
    stub.restore();
  });

  it('shows a chip per attempt with more than one, marks the active one, and emits a pick', async () => {
    const stub = stubRequestClient(runnerClient, () => ({
      lease_id: 'lease_b',
      session_id: 'sess',
      available: true,
      reason: null,
      truncated: false,
      turns: [],
    }));
    const fixture = TestBed.createComponent(ChunkTranscriptsTab);
    fixture.componentRef.setInput('attemptsState', 'ready');
    fixture.componentRef.setInput('attemptOptions', [LEASE_A, LEASE_B]);
    fixture.componentRef.setInput('activeAttemptLeaseId', 'lease_b');
    let picked: string | undefined;
    fixture.componentInstance.selectAttempt.subscribe((leaseId) => (picked = leaseId));
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const chips = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(chips).toHaveLength(2);
    expect(chips[1].getAttribute('aria-pressed')).toBe('true');

    (chips[0] as HTMLElement).click();
    expect(picked).toBe('lease_a');
    stub.restore();
  });
});

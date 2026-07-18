import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { TranscriptPanel } from './transcript-panel';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

async function render(
  leaseId: string | null,
  route: (method: string, path: string) => unknown,
): Promise<{ el: HTMLElement; fixture: ComponentFixture<TranscriptPanel> }> {
  stub = stubRequestClient(runnerClient, route);
  await TestBed.configureTestingModule({
    imports: [TranscriptPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(TranscriptPanel);
  fixture.componentRef.setInput('leaseId', leaseId);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

describe('TranscriptPanel', () => {
  it('shows SELECT AN AGENT with no lease selected — and fires no request', async () => {
    const { el } = await render(null, () => ({}));

    expect(el.querySelector('[data-testid="transcript-empty"]')?.textContent).toContain('SELECT AN AGENT');
    expect(stub?.requests).toHaveLength(0);
  });

  it('shows LOADING TRANSCRIPT… before the read resolves', async () => {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? { lease_id: 'L-903', session_id: 'sess-77', available: true, reason: null, truncated: false, turns: [] }
        : {},
    );
    await TestBed.configureTestingModule({
      imports: [TranscriptPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TranscriptPanel);
    fixture.componentRef.setInput('leaseId', 'L-903');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="transcript-loading"]')?.textContent).toContain('LOADING TRANSCRIPT');
  });

  it('shows a distinct error line on a 503 — never mistaken for the empty state', async () => {
    const { el } = await render('L-903', (method, path) => {
      if (method === 'GET' && path === '/api/leases/L-903/transcript') return stubError(503, { detail: 'not wired' });
      return {};
    });

    const errorEl = el.querySelector('[data-testid="transcript-error"]');
    expect(errorEl?.textContent).toContain('TRANSCRIPT UNAVAILABLE');
    expect(el.querySelector('[data-testid="transcript-empty"]')).toBeNull();
  });

  it('shows the spawning state, colored non-alarming, when the lease has no session yet', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? { lease_id: 'L-903', session_id: null, available: false, reason: 'spawning', truncated: false, turns: [] }
        : {},
    );

    const spawningEl = el.querySelector('[data-testid="transcript-spawning"]');
    expect(spawningEl?.textContent).toContain('AGENT STARTING');
    expect(spawningEl?.classList.contains('error')).toBe(false);
  });

  it('shows the not-found state — non-alarming, distinct from a genuine error — with the session id', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? { lease_id: 'L-903', session_id: 'sess-77', available: false, reason: 'not_found', truncated: false, turns: [] }
        : {},
    );

    const notFoundEl = el.querySelector('[data-testid="transcript-not-found"]');
    expect(notFoundEl?.textContent).toContain('NO TRANSCRIPT ON DISK');
    expect(notFoundEl?.textContent).toContain('sess-77');
    expect(notFoundEl?.classList.contains('error')).toBe(false);
  });

  it('shows the unreadable state as a genuine fault', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? { lease_id: 'L-903', session_id: 'sess-77', available: false, reason: 'unreadable', truncated: false, turns: [] }
        : {},
    );

    const unreadableEl = el.querySelector('[data-testid="transcript-unreadable"]');
    expect(unreadableEl?.textContent).toContain('TRANSCRIPT UNREADABLE');
    expect(unreadableEl?.classList.contains('error')).toBe(true);
  });

  it('shows a distinct unknown-state fallback for an unrecognized reason — never mistaken for not-found', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? { lease_id: 'L-903', session_id: 'sess-77', available: false, reason: null, truncated: false, turns: [] }
        : {},
    );

    const unknownEl = el.querySelector('[data-testid="transcript-unknown"]');
    expect(unknownEl?.textContent).toContain('UNKNOWN');
    expect(el.querySelector('[data-testid="transcript-not-found"]')).toBeNull();
  });

  it('renders turns in order, kind-classed, with a tool card and a running placeholder for a pending result', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? {
            lease_id: 'L-903',
            session_id: 'sess-77',
            available: true,
            reason: null,
            truncated: false,
            turns: [
              {
                index: 0,
                kind: 'env',
                timestamp: '2026-07-16T11:00:00+00:00',
                text: 'NODE ENVELOPE',
                tool_name: null,
                tool_input: null,
                tool_output: null,
                truncated: false,
              },
              {
                index: 1,
                kind: 'asst',
                timestamp: '2026-07-16T11:00:05+00:00',
                text: 'Starting.',
                tool_name: null,
                tool_input: null,
                tool_output: null,
                truncated: false,
              },
              {
                index: 2,
                kind: 'tool',
                timestamp: '2026-07-16T11:00:10+00:00',
                text: '',
                tool_name: 'Bash',
                tool_input: 'pytest',
                tool_output: null,
                truncated: false,
              },
            ],
          }
        : {},
    );

    const turns = el.querySelectorAll('[data-testid="transcript-turn"]');
    expect(turns).toHaveLength(3);
    expect(turns[0].classList.contains('k-env')).toBe(true);
    expect(turns[0].textContent).toContain('NODE ENVELOPE');
    expect(turns[1].classList.contains('k-asst')).toBe(true);
    expect(turns[1].textContent).toContain('Starting.');
    expect(turns[2].classList.contains('k-tool')).toBe(true);
    expect(turns[2].textContent).toContain('Bash');
    expect(turns[2].textContent).toContain('pytest');
    expect(turns[2].textContent).toContain('running…');
  });

  it('renders the tool output once it resolves, replacing the running placeholder', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? {
            lease_id: 'L-903',
            session_id: 'sess-77',
            available: true,
            reason: null,
            truncated: false,
            turns: [
              {
                index: 0,
                kind: 'tool',
                timestamp: '2026-07-16T11:00:10+00:00',
                text: '',
                tool_name: 'Bash',
                tool_input: 'pytest',
                tool_output: '3 passed',
                truncated: false,
              },
            ],
          }
        : {},
    );

    expect(el.textContent).toContain('3 passed');
    expect(el.textContent).not.toContain('running…');
  });

  it('renders a turn timestamp as a labeled UTC clock time, never bare or local', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? {
            lease_id: 'L-903',
            session_id: 'sess-77',
            available: true,
            reason: null,
            truncated: false,
            turns: [
              {
                index: 0,
                kind: 'env',
                timestamp: '2026-07-16T11:00:00+00:00',
                text: 'NODE ENVELOPE',
                tool_name: null,
                tool_input: null,
                tool_output: null,
                truncated: false,
              },
            ],
          }
        : {},
    );

    expect(el.textContent).toContain('11:00:00 UTC');
  });

  it('shows the truncation banner when the server capped the read', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? { lease_id: 'L-903', session_id: 'sess-77', available: true, reason: null, truncated: true, turns: [] }
        : {},
    );

    expect(el.querySelector('[data-testid="transcript-truncated"]')?.textContent).toContain('TRUNCATED');
  });
});

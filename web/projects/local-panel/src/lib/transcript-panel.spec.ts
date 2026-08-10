import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { TranscriptPanel } from './transcript-panel';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

/** The common shape the archived/hub-unreachable block below shares, varied by one or two
 * fields per case rather than each repeating the whole nine-field body. */
function archivedBody(overrides: Record<string, unknown> = {}) {
  return {
    lease_id: 'L-903',
    session_id: 'sess-77',
    available: true,
    reason: null,
    truncated: false,
    provenance: 'local',
    hub_unreachable: false,
    turns: [],
    ...overrides,
  };
}

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

  it('delegates turns to the shared fleet-transcript-viewer, in order and kind-classed', async () => {
    // Full turn-kind coverage (env/asst/tool/thinking/sidechain, timestamps, caps) is
    // `TranscriptViewer`'s own spec (`fleet/lib/transcripts/transcript-viewer.spec.ts`)
    // now that turn rendering moved there (blizzard#248 D3/D4) — this is a thin smoke
    // test that the container still wires the query's turns through to it.
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
                tool: null,
                thinking_redacted: false,
                sidechain: null,
                truncated: false,
              },
              {
                index: 1,
                kind: 'tool',
                timestamp: '2026-07-16T11:00:10+00:00',
                text: '',
                tool: {
                  name: 'Bash',
                  input: { command: 'pytest' },
                  input_unparsed: null,
                  input_shape: 'object',
                  tool_use_id: 't1',
                  output: null,
                  output_truncated: false,
                },
                thinking_redacted: false,
                sidechain: null,
                truncated: false,
              },
            ],
          }
        : {},
    );

    const turns = el.querySelectorAll('[data-testid="transcript-turn"]');
    expect(turns).toHaveLength(2);
    expect(turns[0].classList.contains('k-env')).toBe(true);
    expect(turns[1].classList.contains('k-tool')).toBe(true);
    expect(turns[1].textContent).toContain('running…');
  });

  it('shows the truncation banner when the server capped the read', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript' ? archivedBody({ truncated: true }) : {},
    );

    expect(el.querySelector('[data-testid="transcript-truncated"]')?.textContent).toContain('TRUNCATED');
  });

  it('shows the archived badge for turns served from the hub, and not for a plain local read (blizzard#249)', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript' ? archivedBody({ provenance: 'archived' }) : {},
    );

    expect(el.querySelector('[data-testid="transcript-archived-badge"]')?.textContent).toContain('ARCHIVED');
  });

  it('shows no archived badge for a plain local read', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript' ? archivedBody() : {},
    );

    expect(el.querySelector('[data-testid="transcript-archived-badge"]')).toBeNull();
  });

  it('shows a distinct hub-unreachable state when the hub could not be asked and local cannot answer either (blizzard#249 D1)', async () => {
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? archivedBody({ available: false, reason: 'not_found', hub_unreachable: true })
        : {},
    );

    const unreachableEl = el.querySelector('[data-testid="transcript-hub-unreachable"]');
    expect(unreachableEl?.textContent).toContain('HUB UNREACHABLE');
    // Never mistaken for the routine no-transcript-yet reading `reason: "not_found"` carries.
    expect(el.querySelector('[data-testid="transcript-not-found"]')).toBeNull();
  });

  it('falls back to a plain local turns read, with no hub-unreachable banner, when the hub is unreachable but local still answers (D1\'s quiet-fallback cell)', async () => {
    // `TranscriptResponse.hub_unreachable`'s own doc (`wire/transcript.py`) states when
    // it's set; this pins that the panel renders the unset case as a plain local turns
    // read, indistinguishable from any other local read, with no hub-unreachable banner.
    const { el } = await render('L-903', (method, path) =>
      method === 'GET' && path === '/api/leases/L-903/transcript'
        ? archivedBody({
            turns: [
              {
                index: 0,
                kind: 'asst',
                timestamp: '2026-07-16T11:00:05+00:00',
                text: 'Still here.',
                tool: null,
                thinking_redacted: false,
                sidechain: null,
                truncated: false,
              },
            ],
          })
        : {},
    );

    expect(el.querySelector('[data-testid="transcript-turns"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="transcript-hub-unreachable"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-archived-badge"]')).toBeNull();
  });
});

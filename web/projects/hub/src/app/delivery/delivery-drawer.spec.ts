import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type ChunkDetail, FLEET_CLOCK } from 'fleet';
import { assetArtifactView, bounceView, chunkDetail, FIXTURE_NOW, fixtureInstant, prView, type RequestClientStub, settle, stubError, stubHubClient, type StubHttpError } from 'fleet/testing';
import { DeliveryDrawer } from './delivery-drawer';

const CHUNK = 'ch_01drawer000000000000000000';

const ENVELOPE = JSON.stringify({
  cause: 'failure',
  detail: 'checks failed in `test`',
  unlanded: [],
  step: { name: 'test', exit_code: 1, output_tail: 'out 19', log_artifact: 'hub-log.land' },
  action: null,
});

describe('DeliveryDrawer', () => {
  let stub: RequestClientStub;

  function configure(detail: ChunkDetail | StubHttpError<number, { detail: string }>): void {
    stub = stubHubClient({ 'GET /api/chunks/{chunk_id}': () => detail });
    TestBed.configureTestingModule({
      imports: [DeliveryDrawer],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: FLEET_CLOCK, useValue: () => FIXTURE_NOW },
      ],
    });
  }

  afterEach(() => stub.restore());

  function create(closable = true) {
    const fixture = TestBed.createComponent(DeliveryDrawer);
    fixture.componentRef.setInput('chunkId', CHUNK);
    fixture.componentRef.setInput('flows', []);
    fixture.componentRef.setInput('repositories', []);
    fixture.componentRef.setInput('closable', closable);
    return fixture;
  }

  it('heads the drawer with the board dock\'s chunk header once the detail is read', async () => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [] }));
    const fixture = create();
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-id"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="delivery-drawer-skeleton"]')).toBeNull();
  });

  it('holds skeletons while the detail is read, then says so when it has nothing to show', async () => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [] }));
    const fixture = create();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="delivery-drawer-skeleton"]')).not.toBeNull();
    expect(el.querySelectorAll('.body fleet-kit-skeleton').length).toBeGreaterThan(0);

    await settle(fixture);
    expect(el.querySelector('[data-testid="delivery-drawer-repo"]')).toBeNull();
    expect(el.querySelector('.side .empty')?.textContent).toContain('Not delivering to any repository yet.');
    expect(el.querySelector('.history .empty')?.textContent).toContain('No delivery yet.');
  });

  it('says so when the detail cannot be read, keeping its close', async () => {
    configure(stubError(500, { detail: 'down' }));
    const fixture = create();
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="delivery-drawer-error"]')?.textContent).toContain("Couldn't read this chunk's detail.");
    expect(el.querySelector('[data-testid="detail-close"]')).not.toBeNull();
  });

  it('draws no close on the phone\'s screen', async () => {
    configure(stubError(500, { detail: 'down' }));
    const fixture = create(false);
    await settle(fixture);

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="detail-close"]')).toBeNull();
  });

  it.each(['{}', ''])('shows the recorded cause in history and its hover when the envelope has no account: %s', async (envelope) => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [], bounces: [bounceView({ cause: 'poll-timeout', envelope })] }));
    const fixture = create();
    await settle(fixture);
    const entries = (fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="delivery-history-entry"]');
    expect(entries).toHaveLength(1);
    const bounce = entries[0].querySelector('.compact-bounce');
    expect(bounce?.querySelector('.detail')?.textContent).toBe('poll-timeout');
    expect(bounce?.getAttribute('title')).toBe('poll-timeout');
  });

  it('reads a bounce stored with a step by its reason without code ticks, drawing no step output', async () => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [], bounces: [bounceView({ cause: 'failure', envelope: ENVELOPE, recorded_at: fixtureInstant(-60) })] }));
    const fixture = create();
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const entry = el.querySelector<HTMLElement>('[data-testid="delivery-history-entry"]')!;

    expect(entry.querySelector('.compact-bounce')?.getAttribute('title')).toBe('checks failed in test');
    expect([...entry.querySelectorAll('.detail')].map((each) => each.textContent?.trim())).toEqual(['checks failed in test']);
    expect(entry.textContent).not.toContain('exited');
    expect(entry.textContent).not.toContain('out 19');
  });

  it('keeps the full bounce hover, repository PR and action log links beside the concise failure', async () => {
    const full = 'PR #42 in org/repo did not land: pre-push answered failure';
    const pr = prView({ repo: 'org/repo', number: 42, url: 'https://example.test/pull/42' });
    configure(chunkDetail({
      chunk_id: CHUNK, history: [], open_prs: [pr],
      bounces: [bounceView({ recorded_at: fixtureInstant(-60), envelope: JSON.stringify({
        cause: 'failure', detail: full,
        unlanded: [{ repo: 'org/repo', branch: 'work', commit: 'abc', pr }],
        action: { name: 'pre-push', choice: 'failure', log_artifact: 'pre-push-log' },
      }) })],
      artifacts: [assetArtifactView({ name: 'pre-push-log', key: 'log-key', recorded_at: fixtureInstant(-60) })],
    }));
    const fixture = create();
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const bounce = el.querySelector('.compact-bounce');
    expect(bounce?.getAttribute('title')).toBe(full);
    expect(bounce?.textContent).toContain('org/repo · PR #42');
    expect(bounce?.textContent).toContain('Pre-push: failure');
    expect(el.querySelector('a.log')?.getAttribute('href')).toBe(`/board/chunk/${CHUNK}/artifact/log-key`);
    expect(el.querySelector(`a[href="${pr.url}"]`)).not.toBeNull();
  });
});

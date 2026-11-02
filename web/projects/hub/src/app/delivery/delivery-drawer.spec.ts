import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, hubClient } from 'fleet';
import { bounceView, chunkDetail, FIXTURE_NOW, fixtureInstant, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { DeliveryDrawer } from './delivery-drawer';
import type { DeliveryRow } from './delivery.model';

const CHUNK = 'ch_01drawer000000000000000000';

const ROW: DeliveryRow = {
  chunk: { chunkId: CHUNK, shortId: 'C-0042', lines: [{ label: 'hub#7', title: 'Fix the lease' }], titlesPending: false, tint: null, projectName: null },
  at: null,
  when: null,
  stage: 0,
  bounce: null,
  line: null,
  step: 'adv-dwf/deliver',
  links: [],
  icon: { lane: 'running', status: 'running' },
  statusColor: '',
};

const TAIL = Array.from({ length: 20 }, (_, i) => `out ${i}`).join('\n');
const ENVELOPE = JSON.stringify({ detail: 'checks failed', step: { name: 'test', exit_code: 1, output_tail: TAIL } });

describe('DeliveryDrawer', () => {
  let stub: RequestClientStub;

  function configure(detail: unknown): void {
    stub = stubRequestClient(hubClient, (_method, path) => (path === `/api/chunks/${CHUNK}` ? detail : {}));
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

  function create() {
    const fixture = TestBed.createComponent(DeliveryDrawer);
    fixture.componentRef.setInput('row', ROW);
    fixture.componentRef.setInput('flows', []);
    fixture.componentRef.setInput('repositories', []);
    return fixture;
  }

  it('heads the drawer with the chunk, its step, and a link to open it', async () => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [] }));
    const fixture = create();
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#delivery-drawer-heading')?.textContent?.trim()).toBe('C-0042');
    expect(el.querySelector('.title-line .title')?.textContent?.trim()).toBe('Fix the lease');
    expect(el.querySelector('.head .dim')?.textContent?.trim()).toBe('adv-dwf/deliver');
    expect(el.querySelector('[data-testid="delivery-drawer-open"]')?.getAttribute('href')).toBe(`/board/chunk/${CHUNK}`);
  });

  it('holds skeletons while the detail is read, then says so when it has nothing to show', async () => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [] }));
    const fixture = create();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelectorAll('fleet-kit-skeleton').length).toBeGreaterThan(0);

    await settle(fixture);
    expect(el.querySelector('.repo')).toBeNull();
    expect(el.querySelector('.side .empty')?.textContent).toContain('Not delivering to any repository yet.');
    expect(el.querySelector('.history .empty')?.textContent).toContain('No delivery yet.');
  });

  it('says so when the detail cannot be read', async () => {
    configure(stubError(500, { detail: 'down' }));
    const fixture = create();
    await settle(fixture);

    expect((fixture.nativeElement as HTMLElement).querySelector('.empty')?.textContent).toContain("Couldn't read this chunk's detail.");
  });

  it('folds a bounce\'s long step output to its last lines, unfolding it on demand', async () => {
    configure(chunkDetail({ chunk_id: CHUNK, history: [], bounces: [bounceView({ cause: 'failure', envelope: ENVELOPE, recorded_at: fixtureInstant(-60) })] }));
    const fixture = create();
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const entry = el.querySelector<HTMLElement>('[data-testid="delivery-history-entry"]')!;
    const tail = () => entry.querySelector('.tail')!.textContent!.split('\n');

    expect(entry.querySelector('.text')?.textContent?.trim()).toBe('bounced · failure');
    expect(entry.querySelector('.detail')?.textContent?.trim()).toBe('checks failed');
    expect(tail()).toHaveLength(12);
    expect(tail().at(-1)).toBe('out 19');

    entry.querySelector<HTMLButtonElement>('.fold')!.click();
    await fixture.whenStable();
    expect(tail()).toHaveLength(20);
    expect(entry.querySelector('.fold')?.textContent?.trim()).toBe('show less');
  });
});

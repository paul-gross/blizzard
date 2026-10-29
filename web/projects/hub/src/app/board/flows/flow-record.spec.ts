import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type FlowFactView, hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { FlowRecord } from './flow-record';

const fact = (id: number, kind: FlowFactView['kind']): FlowFactView => ({
  id,
  flow_id: 'flw_1',
  chunk_id: `ch_01fact${id}00000000000000000000`,
  submission_id: 'sub_a',
  kind,
  outcome: kind === 'turn-ended' ? 'landed' : null,
  revision: null,
  at: '2026-10-29T11:00:00Z',
});

describe('FlowRecord', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (_method, path, search) => {
      if (path === '/api/flows/flw_1') {
        return {
          flow_id: 'flw_1', resource: 'r', repository_id: 'repo_1', repository_name: 'blizzard', repository_slug: null,
          branch: 'master', turn: null, line: [], last_verdict: null, last_landing: null,
        };
      }
      if (path === '/api/flows/flw_1/record') {
        return search.includes('before=2')
          ? { facts: [fact(1, 'submitted')], next_before: null }
          : { facts: [fact(3, 'landed'), fact(2, 'turn-ended')], next_before: 2 };
      }
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [FlowRecord],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  async function render() {
    const fixture = TestBed.createComponent(FlowRecord);
    fixture.componentRef.setInput('flowId', 'flw_1');
    await settle(fixture);
    return fixture;
  }

  const kindsOf = (el: HTMLElement) => [...el.querySelectorAll('[data-testid="flow-record-fact"]')].map((row) => row.getAttribute('data-kind'));

  it('heads the record with the flow\'s lane title and lists its newest facts first', async () => {
    const el = (await render()).nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="flow-record-title"]')?.textContent?.trim()).toBe('blizzard · master');
    expect(kindsOf(el)).toEqual(['landed', 'turn-ended']);
    expect(el.querySelector('[data-testid="flow-record-outcome"]')?.textContent?.trim()).toBe('landed');
  });

  it('reads the facts before the last page\'s next_before, and stops once a page names none', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="flow-record-older"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/flows/flw_1/record', 'GET').map((call) => call.search)).toEqual(['?limit=50', '?before=2&limit=50']);
    expect(kindsOf(el)).toEqual(['landed', 'turn-ended', 'submitted']);
    expect(el.querySelector('[data-testid="flow-record-older"]')).toBeNull();
  });

  it('opens a fact\'s chunk, and closes', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const chunks: string[] = [];
    let closed = 0;
    fixture.componentInstance.selectChunk.subscribe((id) => chunks.push(id));
    fixture.componentInstance.dismiss.subscribe(() => (closed += 1));

    el.querySelector<HTMLElement>('[data-testid="flow-record-chunk"]')?.click();
    el.querySelector<HTMLElement>('[data-testid="flow-record-close"]')?.click();

    expect(chunks).toEqual(['ch_01fact300000000000000000000']);
    expect(closed).toBe(1);
  });
});

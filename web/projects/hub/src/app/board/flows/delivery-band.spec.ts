import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type FlowView, hubClient, hubFlowsKey, toneColor } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { DeliveryBand } from './delivery-band';

const flow = (over: Partial<FlowView> = {}): FlowView => ({
  flow_id: 'flw_1',
  resource: 'repo_1:master',
  repository_id: 'repo_1',
  repository_name: 'blizzard',
  repository_slug: 'paul-gross/blizzard',
  branch: 'master',
  turn: { chunk_id: 'ch_01landing000000000000000000', started_at: '2026-10-29T11:59:00Z' },
  line: [
    { chunk_id: 'ch_01landing000000000000000000', submission_id: 'sub_a', arrival: 1, state: 'landing', position: 1, next_poll_at: null },
    { chunk_id: 'ch_01inline0000000000000000000', submission_id: 'sub_b', arrival: 2, state: 'awaiting-merge' as FlowView['line'][number]['state'], position: 2, next_poll_at: null },
  ],
  last_verdict: null,
  last_landing: null,
  ...over,
});

describe('DeliveryBand', () => {
  let stub: RequestClientStub;
  let flows: FlowView[];
  let queryClient: QueryClient;

  beforeEach(async () => {
    flows = [flow()];
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/flows' ? { flows } : {}));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await TestBed.configureTestingModule({
      imports: [DeliveryBand],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  async function render() {
    const fixture = TestBed.createComponent(DeliveryBand);
    await settle(fixture);
    return fixture;
  }

  it('reads the tenant\'s active flows unlensed, and draws one lane per flow with its landing chunk and its line', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const [call] = stub.forRoute('/api/flows', 'GET');
    expect(call.search).toBe('?active=true');
    expect(el.querySelectorAll('[data-testid="delivery-lane"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="delivery-lane-title"]')?.textContent?.trim()).toBe('blizzard · master');
    expect(el.querySelector('[data-testid="delivery-landing"]')?.getAttribute('data-chunk')).toBe('ch_01landing000000000000000000');
    expect(el.querySelectorAll('[data-testid="delivery-in-line"]')).toHaveLength(1);
  });

  it('renders a state this build does not know raw, in the idle tone', async () => {
    const el = (await render()).nativeElement as HTMLElement;
    const badge = el.querySelector('[data-testid="delivery-in-line"] [data-testid="delivery-state"]');
    expect(badge?.textContent?.trim()).toBe('awaiting-merge');
    expect(badge?.querySelector<HTMLElement>('.badge')?.style.color).toBe(toneColor('idle'));
  });

  it('collapses a lane with nothing landing and no one in line to its last verdict', async () => {
    flows = [
      flow({
        turn: null,
        line: [],
        last_verdict: {
          id: 4, flow_id: 'flw_1', chunk_id: 'ch_a', submission_id: 'sub_a', kind: 'turn-ended',
          outcome: 'landed', revision: null, at: '2026-10-29T11:00:00Z',
        },
      }),
    ];
    const el = (await render()).nativeElement as HTMLElement;
    const lane = el.querySelector('[data-testid="delivery-lane"]');
    expect(lane?.classList).toContain('collapsed');
    expect(lane?.querySelectorAll('[data-testid="delivery-landing"], [data-testid="delivery-in-line"]')).toHaveLength(0);
    expect(lane?.querySelector('[data-testid="delivery-verdict"]')?.textContent).toContain('landed');
  });

  it('drops a lane once the read omits its flow, and renders nothing with no active flow', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="delivery-band"]')).not.toBeNull();

    flows = [];
    await queryClient.invalidateQueries({ queryKey: hubFlowsKey });
    await settle(fixture);

    expect(el.querySelector('[data-testid="delivery-band"]')).toBeNull();
    expect(el.children).toHaveLength(0);
  });

  it('opens a lane\'s chunk, and its title opens the flow', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const chunks: string[] = [];
    const picked: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => chunks.push(id));
    fixture.componentInstance.selectFlow.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="delivery-in-line"]')?.click();
    el.querySelector<HTMLElement>('[data-testid="delivery-lane-title"]')?.click();

    expect(chunks).toEqual(['ch_01inline0000000000000000000']);
    expect(picked).toEqual(['flw_1']);
  });
});

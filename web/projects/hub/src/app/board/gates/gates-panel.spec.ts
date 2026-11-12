import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { DecisionView } from 'fleet';
import { decisionView, settle, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { GatesPanel } from './gates-panel';

const GATES = [
  decisionView({
    decision_id: 'dc_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    node_id: 'nd_01',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-16T00:00:01Z',
    choices: [
      { name: 'approve', description: '', requires_checks: false, would_override_red_checks: false },
      { name: 'reject', description: '', requires_checks: false, would_override_red_checks: false },
    ],
  }),
];

describe('GatesPanel', () => {
  let stub: RequestClientStub;

  const render = async (gates: DecisionView[] | (() => DecisionView[] | Promise<DecisionView[]>) = GATES) => {
    stub = stubHubClient({ 'GET /api/decisions': async () => ({ decisions: typeof gates === 'function' ? await gates() : gates }) });
    await TestBed.configureTestingModule({
      imports: [GatesPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GatesPanel);
    await settle(fixture);
    return fixture;
  };

  afterEach(() => stub.restore());

  it('lists every open gate across the fleet from the fleet-wide decisions read', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(stub.forRoute('/api/decisions', 'GET').length).toBeGreaterThan(0);
    expect(el.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="rail-gate-node"]')?.textContent).toContain('approve-gate');
  });

  it('emits the chunk id when a gate is activated — the gate is resolved in the dock', async () => {
    const fixture = await render();
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));

    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="rail-gate"]')?.click();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('hides the panel only once the fleet has no open gate', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.style.display).toBe('none');
    expect(el.querySelector('[data-testid="gates-panel"]')).toBeNull();
    expect(el.querySelector('[data-testid="gates-empty"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(0);
  });

  it('keeps the loading panel visible until an empty read resolves', async () => {
    let release!: (gates: DecisionView[]) => void;
    const held = new Promise<DecisionView[]>((resolve) => (release = resolve));
    const fixture = await render(() => held);
    const el = fixture.nativeElement as HTMLElement;
    try {
      expect(el.style.display).not.toBe('none');
      expect(el.querySelector('[data-testid="gates-loading"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="gates-empty"]')).toBeNull();
    } finally {
      release([]);
      await settle(fixture);
    }
    expect(el.style.display).toBe('none');
  });

  it('reappears when gates arrive and hides again when they are resolved', async () => {
    let gates: DecisionView[] = [];
    const fixture = await render(() => gates);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.style.display).toBe('none');

    gates = GATES;
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);
    expect(el.style.display).not.toBe('none');
    expect(el.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(1);

    gates = [];
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);
    expect(el.style.display).toBe('none');
  });

  it('shows a failed read instead of treating it as known empty, and recovers', async () => {
    let failed = true;
    const fixture = await render(() => {
      if (failed) throw new Error('decisions unavailable');
      return GATES;
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.style.display).not.toBe('none');
    expect(el.querySelector('[data-testid="gates-error"]')).not.toBeNull();

    failed = false;
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(fixture);
    expect(el.querySelector('[data-testid="gates-error"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(1);
  });
});

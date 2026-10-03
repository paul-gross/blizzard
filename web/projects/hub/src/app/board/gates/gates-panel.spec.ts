import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle, type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { hubClient } from 'fleet';
import { GatesPanel } from './gates-panel';

const GATES = [
  {
    decision_id: 'dc_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    node_id: 'nd_01',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-16T00:00:01Z',
    choices: [
      { name: 'approve', description: '' },
      { name: 'reject', description: '' },
    ],
  },
];

describe('GatesPanel', () => {
  let stub: RequestClientStub;

  const render = async (gates: unknown = GATES) => {
    stub = stubRequestClient(hubClient, (method, path) => (method === 'GET' && path === '/api/decisions' ? { decisions: gates } : {}));
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

  it('rests on an empty state when the fleet has no open gate', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gates-empty"]')).not.toBeNull();
    expect(el.querySelectorAll('[data-testid="rail-gate"]')).toHaveLength(0);
  });
});

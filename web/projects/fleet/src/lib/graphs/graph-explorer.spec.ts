import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { GraphExplorer } from './graph-explorer';

const GRAPHS = [
  { graph_id: 'gr_build_v2', name: 'build', created_at: '2026-07-18T00:00:00Z', effective: true, entry_node_id: 'n1' },
  { graph_id: 'gr_build_v1', name: 'build', created_at: '2026-07-01T00:00:00Z', effective: false, entry_node_id: 'n1' },
  { graph_id: 'gr_review_v1', name: 'review', created_at: '2026-07-10T00:00:00Z', effective: true, entry_node_id: 'n2' },
];

describe('GraphExplorer', () => {
  let stub: RequestClientStub;

  async function mount(graphs: unknown[] = GRAPHS) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/graphs') return graphs;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [GraphExplorer],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphExplorer);
    await settle(fixture);
    return fixture;
  }

  afterEach(() => stub?.restore());

  it('groups graphs by name and shows the version count + effective summary', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    const groups = el.querySelectorAll('[data-testid="graph-explorer-group"]');
    expect(groups).toHaveLength(2);

    const buildGroup = el.querySelector('[data-name="build"]');
    expect(buildGroup?.querySelector('[data-testid="graph-explorer-group-count"]')?.textContent).toContain(
      '2 versions',
    );
    expect(buildGroup?.querySelector('[data-testid="graph-explorer-group-effective"]')?.textContent).toContain(
      'gr_build_v2',
    );
  });

  it('reveals the lineage newest-first with effective/superseded badges on expand', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-lineage"]')).toBeNull();

    const buildToggle = el.querySelector<HTMLButtonElement>(
      '[data-name="build"] [data-testid="graph-explorer-group-toggle"]',
    );
    buildToggle?.click();
    await settle(fixture);

    const rows = el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-graph-id')).toBe('gr_build_v2');
    expect(rows[1].getAttribute('data-graph-id')).toBe('gr_build_v1');

    expect(rows[0].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('effective');
    expect(rows[1].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('superseded');
    // Exact vocabulary — never "active"/"disabled".
    expect(el.textContent).not.toContain('active');
    expect(el.textContent).not.toContain('disabled');
  });

  it('emits selectGraph for either an effective or a superseded row', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await settle(fixture);

    const emitted: string[] = [];
    fixture.componentInstance.selectGraph.subscribe((id: string) => emitted.push(id));

    el.querySelector<HTMLLIElement>('[data-graph-id="gr_build_v1"]')?.click();
    await settle(fixture);

    expect(emitted).toEqual(['gr_build_v1']);
  });

  it('shows a retired badge for a retired, non-effective version (issue #101)', async () => {
    const graphs = [
      { ...GRAPHS[0], effective: false },
      { ...GRAPHS[1], effective: true, retired: false },
      GRAPHS[2],
    ];
    // gr_build_v2 (newest) is retired; gr_build_v1 (older) is now effective.
    const fixture = await mount([{ ...graphs[0], retired: true }, graphs[1], graphs[2]]);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-name="build"] [data-testid="graph-explorer-group-toggle"]')?.click();
    await settle(fixture);

    const rows = el.querySelectorAll('[data-name="build"] [data-testid="graph-explorer-row"]');
    expect(rows[0].getAttribute('data-graph-id')).toBe('gr_build_v2');
    expect(rows[0].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('retired');
    expect(rows[1].querySelector('[data-testid="graph-explorer-badge"]')?.textContent?.trim()).toBe('effective');
  });

  it('shows an empty state when no graphs are minted', async () => {
    const fixture = await mount([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-explorer-groups"]')).toBeNull();
  });
});

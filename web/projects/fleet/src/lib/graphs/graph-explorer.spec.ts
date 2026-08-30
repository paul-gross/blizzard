import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { GraphExplorer } from './graph-explorer';

const GRAPHS = [
  { graph_id: 'gr_build_v2', name: 'build', created_at: '2026-07-18T00:00:00Z', effective: true, entry_node_id: 'n1' },
  { graph_id: 'gr_build_v1', name: 'build', created_at: '2026-07-01T00:00:00Z', effective: false, entry_node_id: 'n1' },
];

describe('GraphExplorer', () => {
  let stub: RequestClientStub;

  async function mount(route: (method: string, path: string) => unknown) {
    stub = stubRequestClient(hubClient, route);
    await TestBed.configureTestingModule({
      imports: [GraphExplorer],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphExplorer);
    return fixture;
  }

  afterEach(() => stub?.restore());

  it('renders a loading state while the graphs read is in flight', async () => {
    const fixture = await mount(() => GRAPHS);
    // A single, un-awaited detectChanges: the query has mounted but its microtask
    // fetch has not yet resolved — the "read in flight" instant, before settle.
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-explorer-groups"]')).toBeNull();
  });

  it('shows an error state when the graphs read fails', async () => {
    const fixture = await mount(() => stubError(500, { detail: 'boom' }));
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-explorer-groups"]')).toBeNull();
  });

  it('shows an empty state when no graphs are minted', async () => {
    const fixture = await mount(() => []);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-explorer-groups"]')).toBeNull();
  });

  it('forwards the resolved graphs and selectedGraphId to the list, and its selectGraph bubbles up', async () => {
    const fixture = await mount(() => GRAPHS);
    fixture.componentRef.setInput('selectedGraphId', 'gr_build_v1');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-explorer-groups"]')).toBeTruthy();
    expect(el.querySelectorAll('[data-testid="graph-explorer-group"]')).toHaveLength(1);
    // The forwarded selectedGraphId reached the list: its group is expanded and the
    // matching row is selected, without a click.
    expect(el.querySelector('[data-graph-id="gr_build_v1"]')?.classList).toContain('selected');

    const emitted: string[] = [];
    fixture.componentInstance.selectGraph.subscribe((id: string) => emitted.push(id));
    el.querySelector<HTMLButtonElement>('[data-graph-id="gr_build_v2"]')?.click();
    await settle(fixture);

    expect(emitted).toEqual(['gr_build_v2']);
  });
});

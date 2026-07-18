import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { GraphDetail } from './graph-detail';

const GRAPH = {
  graph_id: 'gr_build_v2',
  name: 'build',
  enabled: true,
  entry_node_id: 'n_build',
  nodes: [
    {
      node_id: 'n_build',
      name: 'build',
      executor: 'claude',
      session: 'fresh',
      judged_by: 'reviewer',
      mode: 'edit',
      checks: ['lint', 'test'],
      produces: ['branch'],
      retries_max: 3,
      retries_exhausted: 'escalate',
      prompt: 'Build the feature.',
      choices: [{ choice_id: 'c_pass', name: 'pass', description: 'Build succeeded' }],
    },
    {
      node_id: 'n_review',
      name: 'review',
      executor: 'claude',
      session: 'fresh',
      judged_by: 'reviewer',
      choices: [],
    },
  ],
  edges: [
    { from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review', prompt_addendum: 'Focus on tests.' },
  ],
  warnings: [],
};

describe('GraphDetail', () => {
  let stub: RequestClientStub;

  async function mount(graphId: string, route: (m: string, p: string) => unknown) {
    stub = stubRequestClient(hubClient, route);
    await TestBed.configureTestingModule({
      imports: [GraphDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetail);
    fixture.componentRef.setInput('graphId', graphId);
    await settle(fixture);
    return fixture;
  }

  afterEach(() => stub?.restore());

  it('renders the entry node, node table, edges/choices, and prompt text', async () => {
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-entry"]')?.textContent).toContain('build');

    const rows = el.querySelectorAll('[data-testid="graph-detail-node-row"]');
    expect(rows).toHaveLength(2);
    // Scoped to the node table: `data-node-id` now also appears on the diagram's SVG
    // node groups (`graph-diagram.ts`), mounted above the table in the same view.
    const buildRow = el.querySelector('[data-testid="graph-detail-nodes"] [data-node-id="n_build"]') as HTMLElement;
    expect(buildRow.querySelector('[data-testid="graph-detail-entry-badge"]')).toBeTruthy();
    expect(buildRow.textContent).toContain('claude');
    expect(buildRow.textContent).toContain('reviewer');
    expect(buildRow.textContent).toContain('3');
    expect(buildRow.textContent).toContain('escalate');
    expect(buildRow.textContent).toContain('lint, test');
    expect(buildRow.textContent).toContain('branch');

    const edge = el.querySelector('[data-testid="graph-detail-edge"]');
    expect(edge?.querySelector('[data-testid="graph-detail-edge-choice"]')?.textContent).toContain('pass');
    expect(edge?.querySelector('[data-testid="graph-detail-edge-to"]')?.textContent).toContain('review');
    expect(edge?.querySelector('[data-testid="graph-detail-edge-addendum"]')?.textContent).toContain(
      'Focus on tests.',
    );

    const prompt = el.querySelector('[data-testid="graph-detail-prompt-text"]');
    expect(prompt?.textContent).toContain('Build the feature.');
  });

  it('shows an error state for an unknown graph id', async () => {
    const fixture = await mount('gr_missing', () => stubError(404, { detail: 'unknown graph' }));
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-detail-body"]')).toBeNull();
  });
});

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { chunkDetail, graphEdgeView, graphNodeView, graphView, type RequestClientStub, settle, stubError, stubRequestClient, transitionView } from 'fleet/testing';
import { NodeStrip } from './node-strip';

const CHUNK = 'ch_01strip00000000000000000000';

/** A graph of `n` nodes in a line, `n0` → `n1` → …, entered at `n0`. */
const line = (n: number) =>
  graphView({
    graph_id: 'gr_1',
    entry_node_id: 'nd_n0',
    nodes: Array.from({ length: n }, (_, i) => graphNodeView({ node_id: `nd_n${i}`, name: `n${i}` })),
    edges: Array.from({ length: n - 1 }, (_, i) => graphEdgeView({ choice_id: `c${i}`, from_node_id: `nd_n${i}`, to_node_name: `n${i + 1}` })),
  });

describe('NodeStrip', () => {
  let stub: RequestClientStub;

  function configure(graph: unknown, detail: unknown = chunkDetail({ chunk_id: CHUNK, history: [] })): void {
    stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/graphs/gr_1' ? graph : path === `/api/chunks/${CHUNK}` ? detail : {}));
    TestBed.configureTestingModule({
      imports: [NodeStrip],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
  }

  afterEach(() => stub.restore());

  async function render(currentNodeId: string | null = null, graphId: string | null = 'gr_1') {
    const fixture = TestBed.createComponent(NodeStrip);
    fixture.componentRef.setInput('chunkId', CHUNK);
    fixture.componentRef.setInput('graphId', graphId);
    fixture.componentRef.setInput('currentNodeId', currentNodeId);
    await settle(fixture);
    return fixture.nativeElement as HTMLElement;
  }

  const blocks = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.block')];

  it('draws a block per node, lit by the chunk\'s history, the node it stands on current', async () => {
    const history = [transitionView({ epoch: 1, from_node_name: 'n0', to_node_name: 'n1' }), transitionView({ epoch: 2, from_node_name: 'n1', to_node_name: 'n0' })];
    configure(line(4), chunkDetail({ chunk_id: CHUNK, history }));
    const el = await render('nd_n0');

    expect(el.querySelector('[data-testid="node-strip"]')?.getAttribute('aria-label')).toBe('Graph progress');
    expect(blocks(el).map((block) => block.getAttribute('data-state'))).toEqual(['current', 'failed', 'unvisited', 'unvisited']);
    expect(blocks(el)[1].title).toBe('n1 · failed');
  });

  it('breaks the strip into groups of ten, packing it dense past twenty', async () => {
    configure(line(25));
    const el = await render();
    const host = el;

    expect([...el.querySelectorAll('.group')].map((group) => group.querySelectorAll('.block').length)).toEqual([10, 10, 5]);
    expect(host.classList).toContain('dense');
  });

  it('stays loose at twenty blocks', async () => {
    configure(line(20));
    const el = await render();

    expect(el.querySelectorAll('.group')).toHaveLength(2);
    expect(el.classList).not.toContain('dense');
  });

  it('draws nothing until the graph is read — nor for a chunk with no graph, or one whose graph cannot be read', async () => {
    configure(stubError(404, { detail: 'not found' }));
    expect((await render()).querySelector('[data-testid="node-strip"]')).toBeNull();
    expect(stub.forRoute('/api/graphs/gr_1', 'GET')).toHaveLength(1);

    TestBed.resetTestingModule();
    stub.restore();
    configure(line(3));
    expect((await render(null, null)).querySelector('[data-testid="node-strip"]')).toBeNull();
    expect(stub.forRoute('/api/graphs/gr_1', 'GET')).toHaveLength(0);
  });
});

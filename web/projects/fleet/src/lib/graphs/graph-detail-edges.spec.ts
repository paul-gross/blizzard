import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphEdgeView, GraphNodeView } from '../api/hub';
import { GraphDetailEdges } from './graph-detail-edges';

const NODES: GraphNodeView[] = [
  {
    node_id: 'n_build',
    name: 'build',
    executor: 'claude',
    session: 'fresh',
    judged_by: 'reviewer',
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
];

const EDGES: GraphEdgeView[] = [
  { from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review', prompt_addendum: 'Focus on tests.' },
];

describe('GraphDetailEdges', () => {
  async function mount(nodes: readonly GraphNodeView[], edges: readonly GraphEdgeView[]) {
    await TestBed.configureTestingModule({
      imports: [GraphDetailEdges],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetailEdges);
    fixture.componentRef.setInput('nodes', nodes);
    fixture.componentRef.setInput('edges', edges);
    await fixture.whenStable();
    return fixture;
  }

  it('resolves each edge against the choice it fires on, including its prompt addendum', async () => {
    const fixture = await mount(NODES, EDGES);
    const el = fixture.nativeElement as HTMLElement;

    const edge = el.querySelector('[data-testid="graph-detail-edge"]');
    expect(edge?.querySelector('[data-testid="graph-detail-edge-choice"]')?.textContent).toContain('pass');
    expect(edge?.querySelector('[data-testid="graph-detail-edge-to"]')?.textContent).toContain('review');
    expect(edge?.querySelector('[data-testid="graph-detail-edge-addendum"]')?.textContent).toContain(
      'Focus on tests.',
    );
  });

  it('falls back to the raw choiceId when it matches no choice on the source node', async () => {
    const fixture = await mount(NODES, [{ from_node_id: 'n_build', choice_id: 'c_unknown', to_node_name: 'review' }]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-edge-choice"]')?.textContent).toContain('c_unknown');
  });

  it('renders no addendum line when the edge carries none', async () => {
    const fixture = await mount(NODES, [{ from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review' }]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-edge-addendum"]')).toBeNull();
  });

  it('renders no node-edges block for a node with no outgoing edges', async () => {
    const fixture = await mount(NODES, EDGES);
    const el = fixture.nativeElement as HTMLElement;

    const blocks = el.querySelectorAll('[data-testid="graph-detail-node-edges"]');
    expect(blocks).toHaveLength(1);
    expect(blocks[0].getAttribute('data-node-id')).toBe('n_build');
  });
});

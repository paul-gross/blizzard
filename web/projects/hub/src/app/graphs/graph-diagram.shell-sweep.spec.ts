import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { GraphNodeView, GraphView } from 'fleet';
import { GraphDiagram } from './graph-diagram';

/**
 * The diagram's text-measurer contract (`graph-text-measurer.ts`), in real Chromium:
 * every box the production canvas measurer sized fits the text the SVG actually drew.
 * The expected sizes are rebuilt from each `<text>`'s rendered advance width
 * (`getComputedTextLength()`), not from a canvas of the spec's own, which would only
 * prove Chromium's two measurers agree with each other. jsdom has no canvas backend,
 * so `web:unit-test` stubs the measurer and cannot make this claim.
 *
 * Mounts `GraphDiagram` with its production layout and measurer on two graphs written
 * here: a build/review/deliver shape whose `review` node's three-name `produces` list
 * wraps its meta line, and a graph of long node names with bare metas, so the name row,
 * not the meta row, sizes each box and the `name` and `badge` kinds are both bound.
 *
 * Proven able to fail by changing `.node-meta`'s font size in `graph-diagram-node-shape.css`
 * without changing the measurer: the boxes then no longer match the text drawn in them.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

// `graph-box-sizing.ts`'s box-sizing constants, mirrored so the spec rebuilds the sizes independently.
const NAME_PAD_L = 14;
const NAME_GAP = 10;
const BADGE_PAD_X = 6;
const BADGE_GAP_R = 8;
const META_PAD_X = 14;
const MIN_NODE_WIDTH = 150;
const LABEL_PAD_X = 7;
const BASE_NODE_HEIGHT = 60;
const META_LINE_HEIGHT = 15;
// SVG and canvas text measurement agree to a fraction of a pixel, not bit for bit; real defects are whole pixels off.
const SLACK = 1.5;

const NODE_DEFAULTS: Omit<GraphNodeView, 'node_id' | 'name' | 'executor' | 'session' | 'judged_by'> = {
  action: null,
  action_args: {},
  checks: [],
  checks_cwd: null,
  checks_timeout: null,
  choices: [],
  judgement_prompt: null,
  produces: [],
  prompt: null,
  retries_exhausted: null,
  retries_max: null,
  session_source: null,
};

const GRAPH_DEFAULTS: Pick<GraphView, 'artifacts' | 'definition_yaml' | 'follow_latest' | 'retired' | 'sessions' | 'warnings' | 'enabled'> = {
  definition_yaml: '',
  artifacts: [],
  follow_latest: null,
  retired: false,
  sessions: [],
  warnings: [],
  enabled: true,
};

const EDGE_DEFAULTS = { prompt_addendum: null, target_graph: null, target_kind: 'node' } as const;

function choice(id: string, name: string) {
  return { requires_checks: false, choice_id: id, name, description: '' };
}

const DELIVERY: GraphView = {
  ...GRAPH_DEFAULTS,
  graph_id: 'gr_delivery',
  name: 'default-delivery',
  entry_node_id: 'n_build',
  nodes: [
    { ...NODE_DEFAULTS, node_id: 'n_build', name: 'build', executor: 'runner', session: 'fresh', judged_by: 'worker', retries_max: 1, choices: [choice('c_pass', 'pass'), choice('c_retry', 'retry')] },
    {
      ...NODE_DEFAULTS,
      node_id: 'n_review',
      name: 'review',
      executor: 'runner',
      session: 'resume',
      session_source: 'build',
      judged_by: 'worker',
      retries_max: 1,
      produces: [
        { kind: 'asset', name: 'review-findings' },
        { kind: 'asset', name: 'review-summary' },
        { kind: 'asset', name: 'review-transcript' },
      ],
      choices: [choice('c_pass2', 'pass'), choice('c_fail', 'fail')],
    },
    { ...NODE_DEFAULTS, node_id: 'n_deliver', name: 'deliver', executor: 'hub', session: 'fresh', judged_by: 'worker', choices: [choice('c_landed', 'landed'), choice('c_conflict', 'conflict'), choice('c_failure', 'failure')] },
  ],
  edges: [
    { ...EDGE_DEFAULTS, from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_build', choice_id: 'c_retry', to_node_name: 'build' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_review', choice_id: 'c_pass2', to_node_name: 'deliver' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_review', choice_id: 'c_fail', to_node_name: 'build' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_deliver', choice_id: 'c_landed', to_node_name: 'done', target_kind: 'done' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_deliver', choice_id: 'c_conflict', to_node_name: 'build' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_deliver', choice_id: 'c_failure', to_node_name: 'done', target_kind: 'done' },
  ],
};

const LONG_RUNNER = 'a-deliberately-long-node-name-for-measurement';
const LONG_HUB = 'another-long-node-name-for-the-badge';

const NAME_BOUND: GraphView = {
  ...GRAPH_DEFAULTS,
  graph_id: 'gr_name_bound',
  name: 'name-row-bound-measurement',
  entry_node_id: 'n_long_runner',
  nodes: [
    { ...NODE_DEFAULTS, node_id: 'n_long_runner', name: LONG_RUNNER, executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [choice('c_pass', 'pass')] },
    { ...NODE_DEFAULTS, node_id: 'n_long_hub', name: LONG_HUB, executor: 'hub', session: 'fresh', judged_by: 'worker', choices: [choice('c_landed', 'landed'), choice('c_failure', 'failure')] },
  ],
  edges: [
    { ...EDGE_DEFAULTS, from_node_id: 'n_long_runner', choice_id: 'c_pass', to_node_name: LONG_HUB },
    { ...EDGE_DEFAULTS, from_node_id: 'n_long_hub', choice_id: 'c_landed', to_node_name: 'done', target_kind: 'done' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_long_hub', choice_id: 'c_failure', to_node_name: 'done', target_kind: 'done' },
  ],
};

interface MeasuredNode {
  name: string;
  boxWidth: number;
  boxHeight: number;
  nameWidth: number;
  nameRight: number;
  badge: string;
  badgeWidth: number;
  badgeLeft: number;
  metas: { text: string; width: number; overhang: number }[];
}

/** The laid-out geometry beside what the browser rendered, read off the mounted SVG. */
function measure(root: HTMLElement): { nodes: MeasuredNode[]; labels: { text: string; width: number; boxWidth: number }[] } {
  const nodes: MeasuredNode[] = [];
  for (const g of root.querySelectorAll('[data-testid="graph-diagram-node"]')) {
    const box = g.querySelector<SVGGraphicsElement>('path.node-box')!.getBBox();
    const name = g.querySelector<SVGTextElement>('text.node-name')!;
    const badge = g.querySelector<SVGTextElement>('text.node-badge')!;
    const nameBox = name.getBBox();
    nodes.push({
      name: name.textContent!.trim(),
      boxWidth: box.width,
      boxHeight: box.height,
      nameWidth: name.getComputedTextLength(),
      nameRight: nameBox.x + nameBox.width,
      badge: badge.textContent!.trim(),
      badgeWidth: badge.getComputedTextLength(),
      badgeLeft: badge.getBBox().x,
      metas: [...g.querySelectorAll<SVGTextElement>('text.node-meta')].map((t) => {
        const b = t.getBBox();
        return { text: t.textContent!, width: t.getComputedTextLength(), overhang: b.x + b.width - (box.x + box.width) };
      }),
    });
  }
  const labels = [];
  for (const group of root.querySelectorAll('[data-testid="graph-diagram-edge"], [data-testid="graph-diagram-self-loop"]')) {
    const text = group.querySelector<SVGTextElement>('text.edge-label');
    const bg = group.querySelector<SVGGraphicsElement>('rect.edge-label-bg');
    if (text && bg) labels.push({ text: text.textContent!.trim(), width: text.getComputedTextLength(), boxWidth: bg.getBBox().width });
  }
  return { nodes, labels };
}

async function mount(graph: GraphView): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GraphDiagram],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphDiagram);
  fixture.componentRef.setInput('graph', graph);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(1400, 900);
  return root;
}

const nameRow = (node: MeasuredNode) => NAME_PAD_L + node.nameWidth + NAME_GAP + (node.badgeWidth + BADGE_PAD_X * 2) + BADGE_GAP_R;
const metaRow = (node: MeasuredNode) => Math.max(0, ...node.metas.map((meta) => META_PAD_X * 2 + meta.width));

describe('graph-diagram shell sweep (web:shell-sweep)', () => {
  it('sizes every node box, meta line and edge label to the text Chromium drew', async () => {
    let wrapped = false;
    let nameBound = 0;
    for (const graph of [DELIVERY, NAME_BOUND]) {
      const root = await mount(graph);
      try {
        expect(root.querySelector('[data-testid="graph-diagram-fallback"]'), `${graph.name}: fell back`).toBeNull();
        const geometry = measure(root);
        expect(geometry.nodes, `${graph.name}: every node drawn`).toHaveLength(graph.nodes.length);
        for (const node of geometry.nodes) {
          const where = `${graph.name}: node ${node.name}`;
          const expected = Math.max(MIN_NODE_WIDTH, Math.ceil(nameRow(node)), Math.ceil(metaRow(node)));
          expect(Math.abs(node.boxWidth - expected), `${where} is ${node.boxWidth}px wide, its text needs ${expected}px`).toBeLessThanOrEqual(SLACK);
          // Under-measuring the name or the badge drags the right-anchored badge into the name.
          expect(node.badgeLeft - node.nameRight, `${where} crowds its ${node.badge} badge`).toBeGreaterThanOrEqual(NAME_GAP + BADGE_PAD_X * 2 - SLACK);
          const expectedHeight = BASE_NODE_HEIGHT + Math.max(0, node.metas.length - 1) * META_LINE_HEIGHT;
          expect(Math.abs(node.boxHeight - expectedHeight), `${where} has ${node.metas.length} meta lines in a ${node.boxHeight}px box`).toBeLessThanOrEqual(SLACK);
          for (const meta of node.metas) expect(meta.overhang, `${where}: meta line ${meta.text} overflows its box`).toBeLessThanOrEqual(SLACK);
          if (nameRow(node) > Math.max(metaRow(node), MIN_NODE_WIDTH)) nameBound += 1;
          wrapped ||= node.metas.length > 1;
        }
        expect(geometry.labels.length, `${graph.name}: no edge label drawn`).toBeGreaterThan(0);
        for (const label of geometry.labels) {
          const expected = Math.ceil(label.width + LABEL_PAD_X * 2);
          expect(Math.abs(label.boxWidth - expected), `${graph.name}: label ${label.text} background is ${label.boxWidth}px, its text needs ${expected}px`).toBeLessThanOrEqual(SLACK);
        }
        if (graph === DELIVERY) {
          const metas = geometry.nodes.flatMap((node) => node.metas.map((meta) => meta.text));
          expect(metas.some((text) => text.startsWith('resume:build')), 'a targeted resume renders its authored form').toBe(true);
        }
      } finally {
        root.remove();
      }
    }
    // Both sensitivities are load-bearing: a meta line wrapped, and the name row sized at least two boxes.
    expect(wrapped, 'no node wrapped its meta line').toBe(true);
    expect(nameBound, 'nodes sized by their name row').toBeGreaterThanOrEqual(2);
  });
});

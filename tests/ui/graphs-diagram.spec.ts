import { expect, test } from '../harness/fixtures';

// The graph explorer's diagram in a real browser over a live hub: the explorer opens a minted graph's SVG DAG, its START
// connector lands on the entry node, node, edge and self-loop selection drive the detail pane, and every graph the hub
// ships renders a diagram, or the documented fallback only when an edge leaves the graph. The measurer contract the
// boxes are sized by is `web:shell-sweep`'s, in `graph-diagram.shell-sweep.spec.ts`. The graphs are named
// tenant-wide, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

/**
 * A `build -> review -> deliver` graph: an entry node, an advance edge, a retry back-edge, a self-loop retry, mixed
 * executors, and a targeted resume. Its `deliver` node is never run, so it names `land`, an action every hub admits,
 * and authors each outcome `land` can answer.
 */
const DELIVERY_YAML = `name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      build the change
    judgement:
      prompt: |
        judge the build
      choices:
        pass: {description: Committed and green., to: review}
        retry: {description: Needs another pass., to: build}
    retries: {max: 1, exhausted: escalate}
  review:
    executor: runner
    prompt: |
      review the change
    session: resume:build
    produces: [review-findings, review-summary, review-transcript]
    judgement:
      prompt: |
        judge the review
      choices:
        pass: {description: Passes cold-eyes review., to: deliver}
        fail: {description: Blocking issues., to: build}
    retries: {max: 1, exhausted: escalate}
  deliver:
    executor: hub
    action: land
    judgement:
      choices:
        landed: {description: Every repo merged cleanly., to: done}
        conflict: {description: A repo did not merge cleanly., to: build}
        failure: {description: A repo failed to land., to: done}
        inherited-failure: {description: The base branch already fails., to: done}
`;

// The SVG surface the in-page callbacks below touch; the test project compiles without the DOM library.
interface Point { x: number; y: number }
interface Box extends Point { width: number; height: number }
interface SvgNode {
  querySelector(selector: string): SvgNode | null;
  querySelectorAll(selector: string): Iterable<SvgNode>;
  getAttribute(name: string): string | null;
  getBBox(): Box;
  getTotalLength(): number;
  getPointAtLength(length: number): Point;
  getScreenCTM(): { a: number; b: number; c: number; d: number; e: number; f: number } | null;
}

// Where the START connector's path ends, as the node it lands on top of: geometry is all that marks the entry node.
const startLanding = (svg: unknown): string | null => {
  const root = svg as SvgNode;
  const path = root.querySelector('[data-testid="graph-diagram-start-path"]')!;
  const end = path.getPointAtLength(path.getTotalLength());
  for (const g of root.querySelectorAll('[data-testid="graph-diagram-node"]')) {
    const box = g.querySelector('path.node-box')!.getBBox();
    const withinX = end.x >= box.x && end.x <= box.x + box.width;
    const atTop = end.y <= box.y + box.height && end.y >= box.y - 20;
    if (withinX && atTop) return g.getAttribute('data-node-id');
  }
  return null;
};

// A point on the path's own curve, off its midpoint where the label pill sits, in viewport coordinates.
const offsetPointOnPath = (el: unknown): Point => {
  const path = (el as SvgNode).querySelector('path.edge')!;
  const pt = path.getPointAtLength(path.getTotalLength() * 0.2);
  const ctm = path.getScreenCTM()!;
  return { x: ctm.a * pt.x + ctm.c * pt.y + ctm.e, y: ctm.b * pt.x + ctm.d * pt.y + ctm.f };
};

test('test_graphs_diagram_renders_in_the_browser', async ({ api, page, wideViewport }) => {
  const created = await api.post('/api/graphs', { data: { definition_yaml: DELIVERY_YAML } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const { graph_id: graphId, name: graphName } = created.body;
  const entryNodeId = (await api.get(`/api/graphs/${graphId}`)).body.entry_node_id;

  await page.setViewportSize(wideViewport);
  await page.goto('/graphs');
  await expect(page.getByTestId('graph-explorer')).toBeVisible();
  const group = page.locator(`[data-testid="graph-explorer-group"][data-name="${graphName}"]`);
  await expect(group).toHaveCount(1);

  // Groups render collapsed; expanding one selects its effective version.
  await group.getByTestId('graph-explorer-group-toggle').click();
  await expect(page).toHaveURL(new RegExp(`/graphs/${graphId}$`));
  // `data-graph-id` sits on the `<li>` and the testid on the row button inside it.
  const row = group.locator(`[data-graph-id="${graphId}"] [data-testid="graph-explorer-row"]`);
  await expect(row).toBeVisible();
  await row.click();
  await expect(page).toHaveURL(new RegExp(`/graphs/${graphId}$`));
  await expect(page.getByTestId('graph-detail-graph-id')).toHaveText(graphId);

  // A known-valid DAG with a back-edge renders the SVG; the fallback here would be a layout regression.
  const diagram = page.getByTestId('graph-diagram');
  await expect(diagram).toBeVisible();
  const svg = diagram.getByTestId('graph-diagram-svg');
  await expect(svg).toBeVisible();
  await expect(diagram.getByTestId('graph-diagram-fallback')).toHaveCount(0);

  await expect(svg.getByTestId('graph-diagram-node')).toHaveCount(3);
  await expect(svg.getByTestId('graph-diagram-start')).toHaveCount(1);
  expect(await svg.evaluate(startLanding)).toBe(entryNodeId);
  await expect(svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="advance"]').first()).toBeVisible();
  // The review-to-build back-edge is derived `retry`, a kind no wire field carries.
  await expect(svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="retry"]').first()).toBeVisible();
  await expect(svg.getByTestId('graph-diagram-node-name').first()).toContainText('build');
});

test('test_graphs_diagram_selection_in_the_browser', async ({ api, page, wideViewport }) => {
  const created = await api.post('/api/graphs', { data: { definition_yaml: DELIVERY_YAML } });
  expect(created.status).toBe(201);

  await page.setViewportSize(wideViewport);
  await page.goto(`/graphs/${created.body.graph_id}`);
  const svg = page.getByTestId('graph-diagram-svg');
  await expect(svg).toBeVisible();
  const pane = page.getByTestId('graph-diagram-detail');
  await expect(pane.getByTestId('graph-diagram-detail-empty')).toBeVisible();

  // The wire node id is opaque, so the node is found by its rendered name.
  const buildNode = svg.getByTestId('graph-diagram-node').filter({ has: page.getByTestId('graph-diagram-node-name').getByText('build', { exact: true }) });
  await buildNode.click();
  await expect(buildNode).toHaveAttribute('data-selected', 'true');
  // build's incident geometry: three edge groups plus its own self-loop.
  await expect(svg.locator('[data-testid="graph-diagram-edge"][data-incident="true"]')).toHaveCount(3);
  await expect(svg.locator('[data-testid="graph-diagram-self-loop"][data-incident="true"]')).toHaveCount(1);
  const nodePane = pane.getByTestId('graph-diagram-detail-node');
  await expect(nodePane).toBeVisible();
  await expect(nodePane).toContainText('build');
  await expect(pane.getByTestId('graph-diagram-detail-prompt')).toContainText('build the change');

  // A point on the advance edge's curve, off its midpoint, selects the edge.
  const advance = svg.locator('[data-testid="graph-diagram-edge"][data-edge-kind="advance"]').first();
  const point = await advance.evaluate(offsetPointOnPath);
  await page.mouse.click(point.x, point.y);
  await expect(advance).toHaveAttribute('data-selected', 'true');
  await expect(pane.getByTestId('graph-diagram-detail-edge')).toBeVisible();

  // The self-loop selects on the same terms.
  const selfLoop = svg.getByTestId('graph-diagram-self-loop');
  await expect(selfLoop).toHaveCount(1);
  const loopPoint = await selfLoop.evaluate(offsetPointOnPath);
  await page.mouse.click(loopPoint.x, loopPoint.y);
  await expect(selfLoop).toHaveAttribute('data-selected', 'true');
  await expect(pane.getByTestId('graph-diagram-detail-edge')).toContainText('retry');

  // Empty canvas clears the selection and restores the neutral hint.
  const box = (await svg.boundingBox())!;
  await page.mouse.click(box.x + 5, box.y + 5);
  await expect(selfLoop).not.toHaveAttribute('data-selected', 'true');
  await expect(pane.getByTestId('graph-diagram-detail-empty')).toBeVisible();
});

test('test_diagram_geometry_matches_the_rendered_text', async ({ api, page, wideViewport }) => {
  // Every graph the hub ships, enumerated from the hub itself, so a newly shipped graph is covered the day it lands.
  const sync = await api.post('/api/graphs/sync');
  expect(sync.status).toBe(200);
  expect(sync.body.ok).toBe(true);
  const listed = await api.get('/api/graphs');
  expect(listed.status).toBe(200);
  const graphIds: string[] = listed.body.map((graph: { graph_id: string }) => graph.graph_id);
  expect(graphIds.length).toBeGreaterThan(0);

  await page.setViewportSize(wideViewport);
  let targetedResumeSomewhere = false;
  for (const graphId of graphIds) {
    const graph = (await api.get(`/api/graphs/${graphId}`)).body;
    await page.goto(`/graphs/${graphId}`);
    const diagram = page.getByTestId('graph-diagram');
    await expect(diagram).toBeVisible();
    const svg = diagram.getByTestId('graph-diagram-svg');
    const fallback = diagram.getByTestId('graph-diagram-fallback');
    await expect(svg.or(fallback)).toBeVisible();
    if (await fallback.isVisible()) {
      // `layoutGraph`'s one documented refusal: an edge leaving the graph.
      expect(graph.edges.some((edge: { to_node_name: string }) => edge.to_node_name.startsWith('graph:')), `${graph.name} fell back with every edge inside the graph`).toBe(true);
      continue;
    }
    await expect(svg.getByTestId('graph-diagram-node')).toHaveCount(graph.nodes.length);

    // A targeted resume reaches the meta line in its authored `resume:<node>` form.
    const targeted = graph.nodes.filter((node: { session: string; session_source: string | null }) => node.session === 'resume' && node.session_source);
    if (targeted.length > 0) {
      targetedResumeSomewhere = true;
      await expect(svg.locator('text.node-meta').filter({ hasText: /^resume:/ }).first(), `${graph.name} renders no resume:<node> meta line`).toBeVisible();
    }
  }
  expect(targetedResumeSomewhere, 'no shipped graph exercised targeted-resume metadata').toBe(true);
});

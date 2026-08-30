import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { GraphEdgeView, GraphNodeView } from '../api/hub';
import { GraphDetailEdges } from './graph-detail-edges';
import { GraphDetailHeader } from './graph-detail-header';

/**
 * The Phase-2 `graph-detail` split's own render evidence (`bzh:visual-change-needs-a-
 * render`) — a real-Chromium geometry proof jsdom cannot make, for the one layout
 * decision the split actually changed: before it, `GraphDetailHeader`'s four blocks
 * (identity row, lifecycle actions, action-error line, entry line) were direct
 * children of `.body`'s own `flex-direction: column; gap: 10px` (`graph-detail.css`);
 * after it, the container hands them a single flex slot, so
 * `graph-detail-header.css`'s `:host` has to reproduce that same column/gap
 * internally or the four blocks would render pressed flush together with no gap at
 * all — a regression jsdom's unevaluated `@` rules and non-laid-out flexbox can't see
 * (`web:unit-test` only asserts *which* blocks render, never their real position).
 * `GraphDetailEdges`'s own `.section` column was already self-contained before the
 * move (`graph-detail.css` never laid it out), so it carries no analogous risk, but is
 * swept alongside it as the split's other Phase-2 child.
 *
 * Mounts both components directly with plain inputs — no query double, matching how
 * each is actually used (`bzh:frontend-container-presentational`; `GraphDetail`
 * forwards resolved data into both).
 *
 * Proven able to fail by setting `graph-detail-header.css`'s `:host` `gap` to `0`: the
 * four blocks then sit within a few pixels of each other instead of the ~10px this
 * spec pins.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
async function mountHeader(): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GraphDetailHeader],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphDetailHeader);
  fixture.componentRef.setInput('graphId', 'gr_shellsweep0000000000000000');
  fixture.componentRef.setInput('name', 'build');
  fixture.componentRef.setInput('retired', false);
  fixture.componentRef.setInput('canEdit', true);
  fixture.componentRef.setInput('actionError', 'Retire failed.');
  fixture.componentRef.setInput('entryNodeName', 'build');
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(800, 600);
  return root;
}

const EDGES_NODES: GraphNodeView[] = [
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
    choices: [{ choice_id: 'c_done', name: 'done', description: 'Review passed' }],
  },
];

const EDGES: GraphEdgeView[] = [
  { from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review', prompt_addendum: 'Focus on tests.' },
  { from_node_id: 'n_review', choice_id: 'c_done', to_node_name: 'done' },
];

async function mountEdges(): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GraphDetailEdges],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphDetailEdges);
  fixture.componentRef.setInput('nodes', EDGES_NODES);
  fixture.componentRef.setInput('edges', EDGES);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(800, 600);
  return root;
}

describe('graph-detail-header / graph-detail-edges shell sweep (web:shell-sweep)', () => {
  it("keeps GraphDetailHeader's identity row, lifecycle actions, error line, and entry line genuinely stacked with a real gap", async () => {
    const root = await mountHeader();
    try {
      const hdr = root.querySelector<HTMLElement>('.gd-hdr')!;
      const actions = root.querySelector<HTMLElement>('[data-testid="graph-detail-retire"]')!;
      const error = root.querySelector<HTMLElement>('[data-testid="graph-detail-lifecycle-error"]')!;
      const entry = root.querySelector<HTMLElement>('[data-testid="graph-detail-entry"]')!;

      const hdrRect = hdr.getBoundingClientRect();
      const actionsRect = actions.getBoundingClientRect();
      const errorRect = error.getBoundingClientRect();
      const entryRect = entry.getBoundingClientRect();

      // Genuinely a column: each block sits below the previous one, not overlapping.
      expect(actionsRect.top, "lifecycle actions did not land below the identity row").toBeGreaterThanOrEqual(
        hdrRect.bottom,
      );
      expect(errorRect.top, 'the error line did not land below lifecycle actions').toBeGreaterThanOrEqual(
        actionsRect.bottom,
      );
      expect(entryRect.top, 'the entry line did not land below the error line').toBeGreaterThanOrEqual(
        errorRect.bottom,
      );

      // A real gap survives the move — not the flush-together layout a `:host` with no
      // `gap` (or `display: block`'s default) would produce.
      expect(
        actionsRect.top - hdrRect.bottom,
        `the gap between the identity row and lifecycle actions (${actionsRect.top - hdrRect.bottom}px) collapsed — :host's flex gap did not survive the split`,
      ).toBeGreaterThan(4);
      expect(
        errorRect.top - actionsRect.bottom,
        `the gap between lifecycle actions and the error line (${errorRect.top - actionsRect.bottom}px) collapsed`,
      ).toBeGreaterThan(4);
      expect(
        entryRect.top - errorRect.bottom,
        `the gap between the error line and the entry line (${entryRect.top - errorRect.bottom}px) collapsed`,
      ).toBeGreaterThan(4);
    } finally {
      root.remove();
    }
  });

  it("keeps GraphDetailEdges's per-node edge blocks genuinely stacked, with the prompt addendum resolving below its own edge row", async () => {
    const root = await mountEdges();
    try {
      const blocks = root.querySelectorAll<HTMLElement>('[data-testid="graph-detail-node-edges"]');
      expect(blocks, 'fixture defect: expected one node-edges block per node with outgoing edges').toHaveLength(2);

      const firstRect = blocks[0].getBoundingClientRect();
      const secondRect = blocks[1].getBoundingClientRect();
      expect(
        secondRect.top,
        'the second node-edges block did not land below the first — the section column collapsed',
      ).toBeGreaterThanOrEqual(firstRect.bottom);

      // The addendum genuinely renders in flow below its own edge row, not overlapping it.
      const edgeRow = blocks[0].querySelector<HTMLElement>('[data-testid="graph-detail-edge-to"]')!;
      const addendum = blocks[0].querySelector<HTMLElement>('[data-testid="graph-detail-edge-addendum"]')!;
      expect(
        addendum.getBoundingClientRect().top,
        'the addendum did not land below its own edge row',
      ).toBeGreaterThanOrEqual(edgeRow.getBoundingClientRect().bottom);
    } finally {
      root.remove();
    }
  });
});

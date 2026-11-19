import { Component, provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ChunkPage, ChunkGeneralTab, ChunkTranscriptsTab, type hubApi, ViewportService } from 'fleet';
import {
  assetArtifactView,
  chunkDetail,
  chunkEscalationView,
  chunkUsageTotalView,
  chunkUsageView,
  decisionView,
  OPERATOR_ME_RESPONSE,
  questionView,
  recordingAwaitingHumanActions,
  settle,
  stubError,
  stubHubClient,
  transcriptSegmentContentView,
  transcriptSegmentIndexEntry,
  transitionView,
  turnSegmentView,
  workRefView,
} from 'fleet/testing';
import { page } from 'vitest/browser';
import { HUB_CHUNK_PAGE_PROVIDERS } from './hub-chunk-actions';
import { ChunkDetailPanel } from '../chunk-dock/chunk-detail-panel';
/** The chunk page's layout under a real browser — see `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep. */
const DETAIL: hubApi.ChunkDetail = chunkDetail({
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  status: 'running',
  work_refs: [workRefView({ source: 'blizzard', ref: '26', web_url: null })],
  history: [
    transitionView({
      choice_name: null,
      epoch: 1,
      from_node_id: null,
      to_node_id: 'nd_build',
      to_node_name: 'build',
      recorded_at: '2026-07-16T11:00:00.000Z',
    }),
  ],
  artifacts: [],
});

/** A multiline ask with an unbroken path wider than a phone. */
const ASK_DETAIL: hubApi.ChunkDetail = {
  ...DETAIL,
  status: 'waiting_on_human',
  questions: [
    questionView({
      question_id: 'qn_long',
      chunk_id: DETAIL.chunk_id,
      question:
        'Issue #214 does not reproduce under any condition I can test. Details:\n\n' +
        '1. Code trace: the mutation already invalidates both query keys.\n' +
        '2. Live browser test: the board updated within 500ms.\n\n' +
        'The failing path is web/projects/fleet/src/lib/chunk/awaiting-human/chunk-awaiting-human.spec.ts\n\n' +
        'How should I proceed?',
      options: [],
      epoch: 2,
      runner_id: 'rn_01',
      asked_at: '2026-07-16T11:30:00.000Z',
      answered: false,
    }),
  ],
};
/** Both live controls and the answered trail must coexist without being promoted together. */
const SPLIT_DETAIL: hubApi.ChunkDetail = {
  ...ASK_DETAIL,
  questions: [
    ...ASK_DETAIL.questions!,
    questionView({
      question_id: 'qn_answered', chunk_id: DETAIL.chunk_id, question: 'Previously asked?',
      answered: true, answer: 'Yes', answered_by: 'operator', answered_at: '2026-07-16T11:31:00Z',
    }),
  ],
  decision: decisionView({
    decision_id: 'de_01', chunk_id: DETAIL.chunk_id, node_id: 'nd_review', node_name: 'review',
    choices: [{ name: 'approve', description: 'Approve', requires_checks: false, would_override_red_checks: false }],
    check_results: [], transitioned: false,
  }),
};
function expectSplitOrder(root: HTMLElement, workSelector: string, issuesSelector: string, width: number): void {
  const selectors = ['[data-testid="open-question"]', workSelector, issuesSelector,
    '[data-testid="answered-questions"]', '[data-testid="open-decision"]'];
  const elements = selectors.map((selector) => {
    const matches = root.querySelectorAll<HTMLElement>(selector);
    expect(matches.length, `${width}px: ${selector} must occur once`).toBe(1);
    return matches[0];
  });
  const boxes = elements.map((element) => element.getBoundingClientRect());
  for (let i = 1; i < boxes.length; i += 1) {
    expect(boxes[i].top, `${width}px: ${selectors[i]} must follow ${selectors[i - 1]}`).toBeGreaterThanOrEqual(boxes[i - 1].bottom);
  }
  if (width >= 720) {
    for (const box of boxes) {
      expect(box.left, `${width}px: section left of work column`).toBeGreaterThanOrEqual(boxes[1].left - 1);
      expect(box.right, `${width}px: section right of work column`).toBeLessThanOrEqual(boxes[1].right + 1);
    }
  }
}
/** Estimate-only figures in the total and timeline. */
const ESTIMATE_DETAIL: hubApi.ChunkDetail = {
  ...DETAIL,
  history: [
    transitionView({ choice_name: 'pass', epoch: 1, from_node_id: 'nd_build', from_node_name: 'build', to_node_id: 'nd_review', to_node_name: 'review', recorded_at: '2026-07-16T11:00:00.000Z' }),
  ],
  cost: chunkUsageTotalView({ input_tokens: 900, output_tokens: 400, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0, cost_partial: false, estimated_cost_usd: 0.07 }),
  usage: [
    chunkUsageView({ node_id: 'nd_build', epoch: 1, kind: 'spawn', model: 'claude-opus-4-8', input_tokens: 900, output_tokens: 400, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: null, estimated_cost_usd: 0.07 }),
  ],
};
/** Long primary and fallback commands exercise both takeover scroll containers. */
const NEEDS_HUMAN_DETAIL: hubApi.ChunkDetail = {
  ...DETAIL,
  status: 'needs_human',
  escalation: chunkEscalationView({
    epoch: 2,
    cause: null,
    detail: null,
    takeover_command:
      'cd /home/blizzard/runner/work/ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9-nd_review-2 && ' +
      'claude --resume 5f9c2e3a-4b7d-4a1e-9c3f-8d2b6a1e7f4c',
    wrapped_takeover_command:
      'blizzard runner takeover ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9 --dir /home/blizzard/runner/data/runtime',
  }),
};
async function render(detail: hubApi.ChunkDetail = DETAIL) {
  await TestBed.configureTestingModule({
    imports: [ChunkGeneralTab],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkGeneralTab);
  fixture.componentRef.setInput('detail', detail);
  fixture.componentRef.setInput('workItems', { status: 'success', items: [] });
  await fixture.whenStable();
  return fixture;
}
describe('chunk page General tab layout shell sweep (web:shell-sweep)', () => {
  it('places open asks first and decisions last around work, issues and answered questions', async () => {
    const fixture = await render(SPLIT_DETAIL);
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('canResolve', true);
    const port = recordingAwaitingHumanActions();
    fixture.componentRef.setInput('awaitingHumanActions', port.actions);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      for (const width of [1024, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        expectSplitOrder(root, '[data-testid="section-work-item"]', '[data-testid="section-issues"]', width);
        expect(root.querySelectorAll('[data-testid="answer-submit"]').length).toBe(1);
        expect(root.querySelectorAll('[data-testid="decision-choice"]').length).toBe(1);
      }
      const input = root.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
      input.value = '  proceed  ';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await fixture.whenStable();
      root.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')!.click();
      root.querySelector<HTMLButtonElement>('[data-testid="decision-choice"]')!.click();
      await vi.waitFor(() => expect(port.resolves).toHaveLength(1));
      expect(port.answers).toEqual([{ chunkId: DETAIL.chunk_id, questionId: 'qn_long', draft: { answer: '  proceed  ' } }]);
      expect(port.resolves).toEqual([{ chunkId: DETAIL.chunk_id, decisionId: 'de_01', choice: 'approve', draft: { note: '' } }]);
    } finally {
      root.remove();
    }
  });
  it('places the same split in the desktop dock left column', async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkDetailPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', SPLIT_DETAIL);
    fixture.componentRef.setInput('renderedStatus', 'waiting_on_human');
    fixture.componentRef.setInput('workItems', { status: 'success', items: [] });
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expectSplitOrder(root, 'fleet-chunk-detail-facts', '[data-testid="issue-pane"]', 1280);
    } finally {
      root.remove();
    }
  });
  it('stacks work item, issues and node history at narrow widths with no horizontal overflow', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const label = `width ${width}`;
        const workItem = root.querySelector<HTMLElement>('[data-testid="section-work-item"]');
        const issues = root.querySelector<HTMLElement>('[data-testid="section-issues"]');
        const history = root.querySelector<HTMLElement>('[data-testid="section-node-history"]');
        expect(workItem, `${label}: no work-item panel`).not.toBeNull();
        expect(issues, `${label}: no issues panel`).not.toBeNull();
        expect(history, `${label}: no node-history panel`).not.toBeNull();
        const rects = [workItem!, issues!, history!].map((el) => el.getBoundingClientRect());
        const tops = rects.map((r) => r.top);
        expect(new Set(tops).size, `${label}: panels did not stack — tops were ${tops.join(', ')}`).toBe(3);
        const lefts = new Set(rects.map((r) => r.left));
        expect(lefts.size, `${label}: stacked panels are not left-aligned — lefts were ${[...lefts].join(', ')}`).toBe(1);
        const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
        expect(
          general.scrollWidth,
          `${label}: General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
        ).toBeLessThanOrEqual(general.clientWidth);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }
    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
  it('renders the work item and timeline session efficiency figures without overflow at ~390px', async () => {
    const fixture = await render(ESTIMATE_DETAIL);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const chunkFigure = root.querySelector<HTMLElement>('[data-testid="chunk-efficiency"]');
      expect(chunkFigure?.textContent?.trim()).toMatch(/^~\$0\.07/);
      const stepFigure = root.querySelector<HTMLElement>('[data-testid="history-step-efficiency"]');
      expect(stepFigure?.textContent?.trim()).toMatch(/^~\$0\.07/);
      const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
      expect(
        general.scrollWidth,
        `General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
      ).toBeLessThanOrEqual(general.clientWidth);
    } finally {
      root.remove();
    }
  });
  it('sits node history beside a shared work-item/issues left column at 1024px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1024, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const workItem = root.querySelector<HTMLElement>('[data-testid="section-work-item"]')!;
      const issues = root.querySelector<HTMLElement>('[data-testid="section-issues"]')!;
      const history = root.querySelector<HTMLElement>('[data-testid="section-node-history"]')!;
      const workItemRect = workItem.getBoundingClientRect();
      const issuesRect = issues.getBoundingClientRect();
      const historyRect = history.getBoundingClientRect();
      expect(
        historyRect.left,
        `node history's left (${historyRect.left}) is not beside the work-item column (right edge ${workItemRect.right})`,
      ).toBeGreaterThanOrEqual(workItemRect.right);
      expect(
        workItemRect.top,
        `work item and issues share a top (${workItemRect.top}) — they are not stacked in the left column`,
      ).not.toBe(issuesRect.top);
      expect(workItemRect.left, 'work item and issues are not left-aligned with each other').toBe(issuesRect.left);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
  it("renders an agent's multi-paragraph ask on its own lines without overflowing a phone", async () => {
    const fixture = await render(ASK_DETAIL);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width ${width}`;
        const ask = root.querySelector<HTMLElement>('[data-testid="question-text"]');
        expect(ask, `${label}: no ask text in the DOM`).not.toBeNull();
        // Compare the same element with and without preserved newlines.
        const preserved = ask!.offsetHeight;
        ask!.style.whiteSpace = 'normal';
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const collapsed = ask!.offsetHeight;
        ask!.style.whiteSpace = '';
        await new Promise((resolve) => requestAnimationFrame(resolve));
        expect(
          preserved,
          `${label}: ask rendered no taller than its own collapsed rendering (${preserved} vs ${collapsed}) — newlines were not preserved`,
        ).toBeGreaterThan(collapsed);
        // The long unbroken path must break rather than push the dock sideways.
        expect(
          ask!.scrollWidth,
          `${label}: ask text overflows horizontally (${ask!.scrollWidth} > ${ask!.clientWidth})`,
        ).toBeLessThanOrEqual(ask!.clientWidth);
        const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
        expect(
          general.scrollWidth,
          `${label}: General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
        ).toBeLessThanOrEqual(general.clientWidth);
      }
    } finally {
      root.remove();
    }
  });
  it('keeps a long wrapped takeover command and its raw fallback scrollable, not clipped, at 320px', async () => {
    const fixture = await render(NEEDS_HUMAN_DETAIL);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(320, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      // The panel clips horizontally, so each long command must itself scroll.
      const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
      expect(
        general.scrollWidth,
        `General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
      ).toBeLessThanOrEqual(general.clientWidth);

      const primary = root.querySelector<HTMLElement>('[data-testid="takeover-command"]')!;
      expect(
        primary.scrollWidth,
        'fixture defect: wrapped command fits 320px, so scrollability is unprovable',
      ).toBeGreaterThan(primary.clientWidth);
      primary.scrollLeft = 99999;
      expect(primary.scrollLeft, 'wrapped command is clipped, not scrollable').toBeGreaterThan(0);

      const fallback = root.querySelector<HTMLDetailsElement>('[data-testid="takeover-command-raw-fallback"]');
      expect(fallback, 'no raw fallback disclosure in the DOM').not.toBeNull();
      fallback!.open = true;
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const raw = fallback!.querySelector<HTMLElement>('.cmd')!;
      expect(
        raw.scrollWidth,
        'fixture defect: raw fallback fits 320px, so scrollability is unprovable',
      ).toBeGreaterThan(raw.clientWidth);
      raw.scrollLeft = 99999;
      expect(raw.scrollLeft, 'raw fallback is clipped, not scrollable').toBeGreaterThan(0);

      expect(
        general.scrollWidth,
        `expanded: General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
      ).toBeLessThanOrEqual(general.clientWidth);
    } finally {
      root.remove();
    }
  });
});
/** Chromium proves the transcripts nav collapses above its viewer on phones. */
describe('chunk page Transcripts tab layout shell sweep (web:shell-sweep)', () => {
  it('stacks the step nav above the segment viewer at 390px with no horizontal overflow', async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTranscriptsTab],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkTranscriptsTab);
    fixture.componentRef.setInput('history', [
      transitionView({ from_node_id: 'nd_build', from_node_name: 'build', to_node_id: 'nd_review', to_node_name: 'review', choice_name: null, epoch: 1, recorded_at: '2026-08-09T00:00:00+00:00' }),
    ]);
    fixture.componentRef.setInput('segments', [
      {
        segment_id: 'sg_1',
        node_id: 'nd_build',
        epoch: 1,
        spawn_generation: 0,
        turn_range_start: 0,
        turn_range_end: 1,
        final: true,
        truncated: false,
        byte_count: 40,
        normalizer_version: 'v1',
        harness_version: null,
        received_at: '2026-08-09T00:00:00+00:00',
      },
    ]);
    fixture.componentRef.setInput('indexState', 'ready');
    fixture.componentRef.setInput('segmentId', 'sg_1');
    fixture.componentRef.setInput('segmentState', 'ready');
    fixture.componentRef.setInput('segmentData', {
      segment_id: 'sg_1',
      final: true,
      truncated: false,
      turns: [
        {
          index: 0,
          kind: 'asst',
          timestamp: null,
          text: 'a narrow-viewport turn, long enough to prove wrapping rather than overflow',
          tool: null,
          thinking_redacted: false,
          sidechain: null,
          truncated: false,
        },
      ],
    });
    await fixture.whenStable();

    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const tab = root.querySelector<HTMLElement>('[data-testid="chunk-transcripts-tab"]');
      const nav = root.querySelector<HTMLElement>('[data-testid="transcripts-tab-nav"]');
      const body = root.querySelector<HTMLElement>('[data-testid="transcript-segment-body"]');
      expect(tab, 'no chunk-transcripts-tab in the DOM').not.toBeNull();
      expect(nav, 'no transcripts-tab-nav in the DOM').not.toBeNull();
      expect(body, 'no transcript-segment-body in the DOM').not.toBeNull();

      expect(nav!.getBoundingClientRect().top).toBeLessThan(body!.getBoundingClientRect().top);
      expect(
        tab!.scrollWidth,
        `Transcripts tab overflows horizontally (${tab!.scrollWidth} > ${tab!.clientWidth})`,
      ).toBeLessThanOrEqual(tab!.clientWidth);
    } finally {
      root.remove();
    }
  });
});
/**
 * Pump change detection until `ready()` holds, without `settle()`'s `whenStable()`: a
 * query enabled only once an earlier one resolves registers a pending task zoneless
 * stability never retires, so `whenStable()` would wait forever. Fails loudly if `ready()`
 * never holds.
 */
async function pumpUntil(fixture: ComponentFixture<unknown>, ready: () => boolean, tries = 60): Promise<void> {
  for (let i = 0; i < tries; i += 1) {
    fixture.detectChanges();
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  fixture.detectChanges();
  if (!ready()) throw new Error('pumpUntil: the awaited content never rendered');
}

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const CHAIN_DETAIL: hubApi.ChunkDetail = chunkDetail({
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_build',
  current_node_name: 'build',
  latest_epoch: 1,
  status: 'running',
  work_refs: [],
  history: [],
  artifacts: [],
});

@Component({ selector: 'app-board-stub', template: '' })
class ChainBoardStub {}

const CHAIN_ROUTES = [
  { path: 'board', component: ChainBoardStub },
  { path: 'board/chunk/:chunkId', component: ChunkPage, providers: HUB_CHUNK_PAGE_PROVIDERS },
];

/** Give the router fixture root the real app layout's bounded flex context. */
function mountInAppShell(root: HTMLElement): void {
  root.style.cssText = 'display: flex; flex-direction: column; height: 100%; min-height: 0; overflow: hidden;';
  document.body.appendChild(root);
}

/** A segment long enough to overflow the phone viewport. */
function overflowingTurns(): hubApi.TurnSegmentViewOutput[] {
  return Array.from({ length: 60 }, (_, i) =>
    turnSegmentView({
      index: i,
      kind: 'asst',
      timestamp: null,
      text: `turn ${i} — long enough, repeated enough times, to overflow a phone-height viewport on its own`,
      tool: null,
      thinking_redacted: false,
      sidechain: null,
      truncated: false,
    }),
  );
}

type HubRoutes = Parameters<typeof stubHubClient>[0];

/** The routes the composed chain reads — the operator, the chunk, its work items — plus the test's transcript routes. */
function chainRoutes(detail: hubApi.ChunkDetail, extra: HubRoutes): HubRoutes {
  return {
    'GET /api/me': () => OPERATOR_ME_RESPONSE,
    'GET /api/chunks/{chunk_id}': () => detail,
    'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
    ...extra,
  };
}

describe('chunk page Transcripts tab composed-chain layout shell sweep (web:shell-sweep, review:F1/F2)', () => {
  it("keeps a long segment's own view scrollable, bounded by the tab's real box, not clipped with no scroll container (review:F1)", async () => {
    const stub = stubHubClient(
      chainRoutes(CHAIN_DETAIL, {
        'GET /api/chunks/{chunk_id}/transcripts': () => ({
          chunk_id: CHUNK_ID,
          segments: [
            transcriptSegmentIndexEntry({
              segment_id: 'sg_1',
              node_id: 'nd_build',
              epoch: 1,
              spawn_generation: 0,
              turn_range_start: 0,
              turn_range_end: 60,
              final: true,
              truncated: false,
              byte_count: 4000,
              normalizer_version: 'v1',
              harness_version: null,
              received_at: '2026-08-09T00:00:00+00:00',
            }),
          ],
        }),
        'GET /api/chunks/{chunk_id}/transcripts/{segment_id}': () =>
          transcriptSegmentContentView({ segment_id: 'sg_1', final: true, truncated: false, turns: overflowingTurns() }),
      }),
    );

    try {
      await TestBed.configureTestingModule({
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter(CHAIN_ROUTES),
        ],
      }).compileComponents();
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts&segment=sg_1`);
      const root = harness.fixture.nativeElement as HTMLElement;
      mountInAppShell(root);
      await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="transcript-segment-body"]') !== null);

      try {
        await page.viewport(390, 700);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const tab = root.querySelector<HTMLElement>('[data-testid="chunk-transcripts-tab"]');
        const txView = root.querySelector<HTMLElement>('[data-testid="transcript-segment-body"]')?.parentElement ?? null;
        expect(tab, 'no chunk-transcripts-tab in the composed chain').not.toBeNull();
        expect(txView, 'no scroll section around the segment body').not.toBeNull();

        // An unbounded tab would grow to fit every turn.
        expect(
          tab!.getBoundingClientRect().height,
          `the tab's own box is unbounded (${tab!.getBoundingClientRect().height}px) — the flex/height chain never reached it`,
        ).toBeLessThanOrEqual(700);

        // The bounded detail slot must actually scroll to the tail.
        expect(
          txView!.scrollHeight,
          'the segment view never overflows its own box — the long segment fixture is not actually long enough',
        ).toBeGreaterThan(txView!.clientHeight);
        txView!.scrollTop = txView!.scrollHeight;
        expect(txView!.scrollTop, 'the segment view is clipped, not scrollable').toBeGreaterThan(0);
      } finally {
        root.remove();
      }
    } finally {
      stub.restore();
    }
  });

  it("centers the tab's own permission-denied state inside the tab, not the browser viewport (review:F2)", async () => {
    const stub = stubHubClient({
      ...chainRoutes(CHAIN_DETAIL, {
        'GET /api/chunks/{chunk_id}/transcripts': () => stubError(403, { detail: 'forbidden' }),
      }),
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [] }),
    });

    try {
      await TestBed.configureTestingModule({
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter(CHAIN_ROUTES),
        ],
      }).compileComponents();
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
      await settle(harness.fixture);

      const root = harness.fixture.nativeElement as HTMLElement;
      mountInAppShell(root);
      await settle(harness.fixture);

      try {
        await page.viewport(390, 900);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const host = root.querySelector<HTMLElement>('fleet-chunk-transcripts-tab');
        const status = root.querySelector<HTMLElement>('[data-testid="transcripts-forbidden"]');
        expect(host, 'no fleet-chunk-transcripts-tab host in the composed chain').not.toBeNull();
        expect(status, 'no transcripts-forbidden status line rendered').not.toBeNull();

        const hostRect = host!.getBoundingClientRect();
        const statusRect = status!.getBoundingClientRect();

        // The status must center on the host, not the viewport.
        expect(hostRect.height, `the tab's own box is unreasonably small (${hostRect.height}px)`).toBeGreaterThan(100);

        const statusCenterY = statusRect.top + statusRect.height / 2;
        const hostCenterY = hostRect.top + hostRect.height / 2;
        const viewportCenterY = window.innerHeight / 2;

        // Compare both centers: containment alone cannot distinguish them.
        expect(
          Math.abs(hostCenterY - viewportCenterY),
          `fixture defect: the tab's center (${hostCenterY}) sits too near the viewport's (${viewportCenterY}) to tell the two apart`,
        ).toBeGreaterThan(30);
        expect(
          Math.abs(statusCenterY - hostCenterY),
          `status line centered on ${statusCenterY}, nearer the viewport's center (${viewportCenterY}) than the tab's own (${hostCenterY}) — it has no positioned ancestor`,
        ).toBeLessThan(Math.abs(statusCenterY - viewportCenterY));
      } finally {
        root.remove();
      }
    } finally {
      stub.restore();
    }
  });
});

const DRILLDOWN_DETAIL: hubApi.ChunkDetail = {
  ...CHAIN_DETAIL,
  history: [transitionView({
    choice_name: 'pass', epoch: 1, from_node_id: 'nd_build', from_node_name: 'build',
    to_node_id: 'nd_review', to_node_name: 'review', recorded_at: '2026-08-09T00:00:00.000Z',
  })],
  artifacts: [assetArtifactView({
    key: 'build.plan.1', kind: 'asset', name: 'plan', node_id: 'nd_build', node_name: 'build', epoch: 1,
    content: 'A deliberately long artifact body that must wrap inside the phone detail pane without widening it.',
    recorded_at: '2026-08-09T00:00:00.000Z',
  })],
};

function expectNoOverflow(element: HTMLElement, label: string): void {
  const widest = Array.from(element.querySelectorAll<HTMLElement>('*'))
    .filter((child) => child.scrollWidth > child.clientWidth)
    .map((child) => `${child.tagName}.${child.className} ${child.scrollWidth}/${child.clientWidth}`)
    .join(', ');
  expect(
    element.scrollWidth,
    `${label} overflows horizontally (${element.scrollWidth} > ${element.clientWidth}); children: ${widest}`,
  ).toBeLessThanOrEqual(element.clientWidth);
}

describe('ChunkPage mobile drill-down composed-chain shell sweep (web:shell-sweep)', () => {
  it('keeps General history-free and each selected tab list-only or detail-only without overflow at phone widths', async () => {
    const stub = stubHubClient(
      chainRoutes(DRILLDOWN_DETAIL, {
        'GET /api/chunks/{chunk_id}/transcripts': () => ({
          chunk_id: CHUNK_ID,
          segments: [transcriptSegmentIndexEntry({
            segment_id: 'sg_1', node_id: 'nd_build', epoch: 1, spawn_generation: 0,
            turn_range_start: 0, turn_range_end: 1, final: true, truncated: false, byte_count: 100,
            normalizer_version: 'v1', harness_version: null, received_at: '2026-08-09T00:00:00.000Z',
          })],
        }),
        'GET /api/chunks/{chunk_id}/transcripts/{segment_id}': () => transcriptSegmentContentView({
          segment_id: 'sg_1', final: true, truncated: false,
          turns: [turnSegmentView({ index: 0, kind: 'asst', timestamp: null, tool: null, thinking_redacted: false, sidechain: null, truncated: false, text: 'A transcript turn long enough to prove the selected mobile pane lays out inside its own width.' })],
        }),
      }),
    );

    try {
      await TestBed.configureTestingModule({
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter(CHAIN_ROUTES),
        ],
      }).compileComponents();
      TestBed.inject(ViewportService).setOverride('mobile');
      const harness = await RouterTestingHarness.create();
      const root = harness.fixture.nativeElement as HTMLElement;
      mountInAppShell(root);

      try {
        for (const width of [390, 320]) {
          await page.viewport(width, 700);
          await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="chunk-general-tab"]') !== null);
          const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
          expect(root.querySelector('[data-testid="section-node-history"]'), `${width}px: General still contains history`).toBeNull();
          expectNoOverflow(general, `${width}px General`);

          await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=node-history`);
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="history-step"]') !== null);
          let nodeHistory = root.querySelector<HTMLElement>('[data-testid="chunk-node-history-tab"]')!;
          expect(
            nodeHistory.querySelector<HTMLElement>('.kmd-detail')?.checkVisibility(),
            `${width}px: node history shows detail beside its list`,
          ).toBe(false);
          expect(root.querySelector('[data-testid="node-history-back"]'), `${width}px: node history list has Back`).toBeNull();
          expectNoOverflow(nodeHistory, `${width}px node history list`);
          root.querySelector<HTMLElement>('[data-testid="history-step"]')?.click();
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="node-history-back"]') !== null);
          nodeHistory = root.querySelector<HTMLElement>('[data-testid="chunk-node-history-tab"]')!;
          expect(root.querySelector<HTMLElement>('[data-testid="history-step"]')?.checkVisibility(), `${width}px: node history detail retains its list`).toBe(false);
          expectNoOverflow(nodeHistory, `${width}px node history detail`);
          root.querySelector<HTMLElement>('[data-testid="node-history-back"]')?.click();
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="history-step"]') !== null);

          await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=artifacts`);
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="artifacts-tab-nav-item"]') !== null);
          let artifacts = root.querySelector<HTMLElement>('[data-testid="chunk-artifacts-panel"]')!;
          expect(root.querySelector('[data-testid="artifacts-tab-artifact"]'), `${width}px: artifact list shows a detail`).toBeNull();
          expect(artifacts.querySelector<HTMLElement>('.kmd-detail')?.checkVisibility(), `${width}px: artifact list shows its detail pane`).toBe(false);
          expect(root.querySelector('[data-testid="artifacts-tab-back"]'), `${width}px: artifact list has Back`).toBeNull();
          expectNoOverflow(artifacts, `${width}px artifact list`);
          root.querySelector<HTMLElement>('[data-testid="artifacts-tab-nav-item"]')?.click();
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="artifacts-tab-back"]') !== null);
          artifacts = root.querySelector<HTMLElement>('[data-testid="chunk-artifacts-panel"]')!;
          expect(root.querySelector<HTMLElement>('[data-testid="artifacts-tab-nav"]')?.checkVisibility(), `${width}px: artifact detail retains its list`).toBe(false);
          expectNoOverflow(artifacts, `${width}px artifact detail`);
          root.querySelector<HTMLElement>('[data-testid="artifacts-tab-back"]')?.click();
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="artifacts-tab-nav-item"]') !== null);

          await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="transcript-segment-item"]') !== null);
          let transcripts = root.querySelector<HTMLElement>('[data-testid="chunk-transcripts-tab"]')!;
          expect(root.querySelector('[data-testid="transcript-segment-body"]'), `${width}px: transcript list shows a detail`).toBeNull();
          expect(transcripts.querySelector<HTMLElement>('.kmd-detail')?.checkVisibility(), `${width}px: transcript list shows its detail pane`).toBe(false);
          expect(root.querySelector('[data-testid="transcript-segment-back"]'), `${width}px: transcript list has Back`).toBeNull();
          expectNoOverflow(transcripts, `${width}px transcript list`);
          root.querySelector<HTMLElement>('[data-testid="transcript-segment-item"]')?.click();
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="transcript-segment-back"]') !== null);
          transcripts = root.querySelector<HTMLElement>('[data-testid="chunk-transcripts-tab"]')!;
          expect(root.querySelector<HTMLElement>('[data-testid="transcripts-tab-nav"]')?.checkVisibility(), `${width}px: transcript detail retains its list`).toBe(false);
          expectNoOverflow(transcripts, `${width}px transcript detail`);
          root.querySelector<HTMLElement>('[data-testid="transcript-segment-back"]')?.click();
          await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="transcript-segment-item"]') !== null);
        }

        TestBed.inject(ViewportService).setOverride('desktop');
        await page.viewport(1024, 700);
        await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
        await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="section-node-history"]') !== null);
        await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=node-history`);
        await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="history-step"]') !== null);
        expect(
          root.querySelector('[data-testid="chunk-node-history-tab"] .kmd-detail'),
          'desktop node history lost its simultaneous detail pane',
        ).not.toBeNull();
        await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=artifacts`);
        await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="artifacts-tab-artifact"]') !== null);
        expect(root.querySelector<HTMLElement>('[data-testid="artifacts-tab-nav"]')?.checkVisibility(), 'desktop artifacts lost its nav').toBe(true);
        await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
        await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="transcript-segment-empty"]') !== null);
        expect(root.querySelector<HTMLElement>('[data-testid="transcripts-tab-nav"]')?.checkVisibility(), 'desktop transcripts lost its nav').toBe(true);
      } finally {
        root.remove();
      }
    } finally {
      stub.restore();
    }
  });
});

import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { stubError } from 'fleet/testing';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { ArtifactPage } from './artifact-page';
import { ChunkPage } from './chunk-page';

/**
 * The chunk detail page (`/board/chunk/:chunkId`, its General and Artifacts
 * tabs) and its deeper single-artifact page. Driven through a real router
 * (`RouterTestingHarness`) rather than a stubbed `ActivatedRoute`: the page
 * reads its own route params *and* query params (the tab selection) *and*
 * renders `routerLink`s, so the route table and the URL round trip are part
 * of what is under test — a chunk row's link must actually resolve to the
 * artifact page, and a tab click must actually write the URL.
 *
 * The hub client's transport is stubbed, so this asserts what the pages compose
 * off a known aggregate, not the queries themselves (those have their own specs).
 */
const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

const DETAIL = {
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  model: 'claude-opus-5',
  status: 'running',
  work_refs: [{ source: 'blizzard', ref: '26', url: null }],
  history: [
    {
      choice_name: 'pass',
      epoch: 1,
      from_node_id: 'nd_build',
      from_node_name: 'build',
      graph_id: 'gr_1',
      graph_name: 'default',
      recorded_at: '2026-07-16T11:00:00.000Z',
      to_node_id: 'nd_review',
      to_node_name: 'review',
    },
  ],
  artifacts: [
    {
      key: 'review.findings.2',
      kind: 'asset',
      name: 'findings',
      node_id: 'nd_review',
      node_name: 'review',
      epoch: 2,
      content: 'THE FINDINGS BODY',
      recorded_at: '2026-07-16T11:30:00.000Z',
    },
    {
      key: 'build.branch.1',
      kind: 'git_commit',
      name: 'branch',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'paul-gross/blizzard',
      branch_name: 'feature/x',
      branch_url: 'https://example.test/branch',
      commit_hash: 'abc1234',
      recorded_at: '2026-07-16T11:10:00.000Z',
    },
  ],
};

/** Stands in for the real board page — only its route resolving matters here,
 * so the back link's `/board?chunk=…` navigation actually lands. */
@Component({ selector: 'app-board-stub', template: '' })
class BoardStub {}

const ROUTES = [
  { path: 'board', component: BoardStub },
  { path: 'board/chunk/:chunkId', component: ChunkPage },
  { path: 'board/chunk/:chunkId/artifact/:artifactKey', component: ArtifactPage },
];

describe('Mobile chunk drill-down', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) {
        return { items: [{ ref: 'blizzard#26', title: 'Make the board mobile', state: 'open', web_url: null }] };
      }
      return DETAIL;
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(ROUTES),
      ],
    });
  });

  afterEach(() => stub.restore());

  async function open(url: string): Promise<HTMLElement> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return harness.fixture.nativeElement as HTMLElement;
  }

  /** Click a tab button by its `KitTabs` testid and let the resulting
   * client-side navigation settle. */
  async function chooseTab(
    harness: RouterTestingHarness,
    testid: 'tab-general' | 'tab-node-history' | 'tab-artifacts',
  ): Promise<HTMLElement> {
    const el = harness.fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`)?.click();
    await settle(harness.fixture);
    return harness.fixture.nativeElement as HTMLElement;
  }

  it('renders the General tab active by default, stacking its sections in attention order', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="board-chunk-detail"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="tab-general"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('[data-testid="tab-artifacts"]')?.getAttribute('aria-selected')).toBe('false');

    const sections = Array.from(el.querySelectorAll('[data-testid^="section-"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(sections).toEqual(['section-work-item', 'section-issues', 'section-node-history', 'section-asks']);
  });

  it('names the chunk by its full id in the identity header, not the compact ref', async () => {
    // The identity header (fleet's shared `ChunkPageHeader`, issue: the runner's own
    // page never had one, and this page's own ref used to be `compactRef`'d down to
    // `C-3YJ9`) — the user wants the full id on a page whose whole job is naming this
    // one chunk.
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    const ref = el.querySelector('[data-testid="mobile-chunk-ref"]');
    expect(ref?.textContent?.trim()).toBe(CHUNK_ID);
    expect(ref?.textContent).not.toContain('C-3YJ9');
    expect(el.querySelector('[data-testid="mobile-chunk-status"]')?.textContent?.trim()).toBe('running');
  });

  it('renders the fleet detail regions verbatim rather than forking them', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('fleet-chunk-detail-facts')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-issue-pane')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-timeline')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-awaiting-human')).not.toBeNull();
    // Artifacts render as links here, never the desktop dock's inline bodies.
    expect(el.querySelector('fleet-chunk-detail-artifacts')).toBeNull();
  });

  it('switches to the Artifacts tab on click, writing ?tab=artifacts with no full reload, and back again', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);

    let el = await chooseTab(harness, 'tab-artifacts');
    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=artifacts`);
    expect(el.querySelector('[data-testid="tab-artifacts"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('[data-testid="artifacts-tab-nav"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="section-work-item"]')).toBeNull();

    el = await chooseTab(harness, 'tab-general');
    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=general`);
    expect(el.querySelector('[data-testid="section-work-item"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="artifacts-tab-nav"]')).toBeNull();
  });

  it('renders the strip General, Node history, Artifacts, Transcripts in that order', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}`);
    const tabs = Array.from(el.querySelectorAll('[data-testid^="tab-"]')).map((n) => n.getAttribute('data-testid'));
    expect(tabs).toEqual(['tab-general', 'tab-node-history', 'tab-artifacts', 'tab-transcripts']);
  });

  it('switches to the Node history tab on click, writing ?tab=node-history, and selecting a row writes ?step=', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);

    const el = await chooseTab(harness, 'tab-node-history');
    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=node-history`);
    expect(el.querySelector('[data-testid="chunk-node-history-tab"]')).not.toBeNull();

    (el.querySelector('[data-testid="selection-step"]') as HTMLButtonElement).click();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=node-history&step=nd_build:1`);
    expect(
      harness.fixture.nativeElement
        .querySelector('[data-testid="selection-step"]')
        ?.classList.contains('selected'),
    ).toBe(true);
  });

  it("shows exactly the selected step's own artifacts on the Node history tab (D8: exact (node_id, epoch))", async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      if (path === `/api/chunks/${CHUNK_ID}/transcripts`) return { chunk_id: CHUNK_ID, segments: [] };
      return DETAIL;
    });
    // DETAIL's own build.branch.1 artifact is (nd_build, epoch 1); review.findings.2 is
    // (nd_review, epoch 2) — selecting the build step must show only the former.
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=node-history&step=nd_build:1`);

    expect(el.querySelectorAll('[data-testid="node-history-artifact-key"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="node-history-artifact-key"]')?.textContent).toContain('build.branch.1');
    expect(el.textContent).not.toContain('review.findings.2');
  });

  it('states an explicit empty transcript for a step with no shipped segments', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      if (path === `/api/chunks/${CHUNK_ID}/transcripts`) return { chunk_id: CHUNK_ID, segments: [] };
      return DETAIL;
    });
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=node-history&step=nd_build:1`);

    expect(el.querySelector('[data-testid="node-history-transcript-empty"]')?.textContent).toContain(
      'NO TRANSCRIPT FOR THIS STEP',
    );
  });

  it("renders the selected step's own transcript once its segment resolves", async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      if (path === `/api/chunks/${CHUNK_ID}/transcripts`) {
        return {
          chunk_id: CHUNK_ID,
          segments: [
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
              received_at: '2026-07-16T11:05:00.000Z',
            },
          ],
        };
      }
      if (path === `/api/chunks/${CHUNK_ID}/transcripts/sg_1`) {
        return {
          segment_id: 'sg_1',
          final: true,
          truncated: false,
          turns: [
            { index: 0, kind: 'asst', text: 'built it', timestamp: null, tool: null, thinking_redacted: false, sidechain: null, truncated: false },
          ],
        };
      }
      return DETAIL;
    });
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=node-history&step=nd_build:1`);

    expect(el.querySelector('[data-testid="node-history-transcript-body"]')?.textContent).toContain('built it');
  });

  it('defaults to the General tab for an absent ?tab value', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}`);
    expect(el.querySelector('[data-testid="tab-general"]')?.getAttribute('aria-selected')).toBe('true');
  });

  it('defaults to the General tab for a garbage ?tab value', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=not-a-real-tab`);
    expect(el.querySelector('[data-testid="tab-general"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('[data-testid="section-work-item"]')).not.toBeNull();
  });

  it('lists every artifact in the Artifacts tab nav, oldest first, defaulting the viewer to the most recent', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=artifacts`);

    const rows = Array.from(el.querySelectorAll<HTMLButtonElement>('[data-testid="artifacts-tab-nav-item"]'));
    expect(rows.map((r) => r.getAttribute('data-artifact-key'))).toEqual(['build.branch.1', 'review.findings.2']);
    // review.findings.2 (11:30) is the more recent of the two (build.branch.1 is 11:10).
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')?.textContent).toContain('THE FINDINGS BODY');
  });

  it('deep-links a specific artifact pre-selected — the dock link’s own contract', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=artifacts&artifact=build.branch.1`);

    const active = el.querySelector('[data-testid="artifacts-tab-nav-item"].active');
    expect(active?.getAttribute('data-artifact-key')).toBe('build.branch.1');
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')?.textContent).toContain('paul-gross/blizzard');
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')?.textContent).not.toContain('THE FINDINGS BODY');
  });

  it('switches the viewer on a nav click, writing the key to the URL with no reload', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=artifacts`);
    await settle(harness.fixture);
    let el = harness.fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')?.textContent).toContain('THE FINDINGS BODY');

    el.querySelector<HTMLButtonElement>('[data-testid="artifacts-tab-nav-item"][data-artifact-key="build.branch.1"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=artifacts&artifact=build.branch.1`);
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')?.textContent).toContain('paul-gross/blizzard');
  });

  it('resolves a stale artifact key to the dead-link empty state rather than a silent fallback', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=artifacts&artifact=gone.missing.9`);

    expect(el.querySelector('[data-testid="artifacts-tab-empty"]')?.textContent).toContain('NO SUCH ARTIFACT');
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')).toBeNull();
  });

  it('opens one asset artifact in full, one level deeper', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}/artifact/review.findings.2`);

    expect(el.querySelector('[data-testid="mobile-artifact-key"]')?.textContent).toContain('review.findings.2');
    expect(el.querySelector('[data-testid="mobile-artifact-content"]')?.textContent).toContain('THE FINDINGS BODY');
  });

  it('renders a git_commit artifact as its pinned repo/branch/commit reference', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}/artifact/build.branch.1`);

    const ref = el.querySelector('[data-testid="mobile-artifact-ref"]')?.textContent ?? '';
    expect(ref).toContain('paul-gross/blizzard');
    expect(ref).toContain('abc1234');
    expect(el.querySelector<HTMLAnchorElement>('[data-testid="mobile-artifact-branch"]')?.getAttribute('href')).toBe(
      'https://example.test/branch',
    );
    expect(el.querySelector('[data-testid="mobile-artifact-content"]')).toBeNull();
  });

  it('reads no artifact-specific route — a stale key is a dead link, not an error', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}/artifact/gone.missing.9`);

    expect(el.querySelector('[data-testid="mobile-artifact-missing"]')?.textContent).toContain('NO SUCH ARTIFACT');
    expect(el.querySelector('[data-testid="mobile-artifact-error"]')).toBeNull();
  });

  it('gives the chunk page a back link to the board, with the originating chunk selected', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector<HTMLAnchorElement>('[data-testid="mobile-chunk-back"]')?.getAttribute('href')).toBe(
      `/board?chunk=${CHUNK_ID}`,
    );
  });

  it('navigates back to the board with the chunk selected on a back-link click', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);
    const el = harness.fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLAnchorElement>('[data-testid="mobile-chunk-back"]')?.click();
    await settle(harness.fixture);

    expect(TestBed.inject(Router).url).toBe(`/board?chunk=${CHUNK_ID}`);
  });

  it('states a terminal chunk\'s status once, not doubled with the unresolvable node sentinel', async () => {
    // A finished chunk's newest transition targets the graph's terminal `done`
    // sentinel, which `current_node_name` cannot resolve (blizzard#203) — the
    // header used to fall back to rendering the literal id "done" beside a
    // status that also reads "done".
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return { ...DETAIL, status: 'done', current_node_id: 'done', current_node_name: null };
    });
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    const header = el.querySelector('[data-testid="board-chunk-detail"] header');
    const occurrences = (header?.textContent ?? '').toLowerCase().match(/done/g) ?? [];
    expect(occurrences).toHaveLength(1);
    expect(el.querySelector('[data-testid="mobile-chunk-node"]')).toBeNull();
  });

  it('says so when the chunk has no artifacts at all', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return { ...DETAIL, artifacts: [] };
    });
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=artifacts`);

    expect(el.querySelector('[data-testid="artifacts-tab-nav-empty"]')?.textContent).toContain('No artifacts yet');
    expect(el.querySelector('[data-testid="artifacts-tab-empty"]')?.textContent).toContain('No artifacts yet');
    expect(el.querySelector('[data-testid="artifacts-tab-nav-item"]')).toBeNull();
  });

  it('reports a failed chunk read rather than spinning on LOADING', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, () => stubError(404, { detail: 'unknown chunk' }));
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="mobile-chunk-error"]')?.textContent).toContain('CHUNK UNAVAILABLE');
    expect(el.querySelector('[data-testid="mobile-chunk-loading"]')).toBeNull();
  });

  it('surfaces an operator action failure instead of swallowing it', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'PATCH') return stubError(409, { detail: 'chunk is not ready' });
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return { ...DETAIL, status: 'not_ready' };
    });
    const harness = await RouterTestingHarness.create();
    const page = await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`, ChunkPage);
    await settle(harness.fixture);

    // Fire the edit the facts pane exposes for a not-ready chunk, through the
    // same handler its output is bound to. The graph edit since issue #144 — the model
    // edit that stood beside it went with `Chunk.model`.
    (page as unknown as { onEditGraph(e: { chunkId: string; graphId: string }): void }).onEditGraph({
      chunkId: CHUNK_ID,
      graphId: 'gr_alt',
    });
    await settle(harness.fixture);

    const el = harness.fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="mobile-chunk-action-error"]')?.textContent).toContain('chunk is not ready');
  });

  // --- Answering from a phone (issue #165) ----------------------------------
  //
  // The mobile board exists so an ask can be answered from a phone, which makes this
  // the surface most likely to *lose* a first-write-wins race — and the one where a
  // misreported race costs the most, since the notice is the only feedback there is.

  const OPEN_QUESTION = {
    question_id: 'qn_77',
    chunk_id: CHUNK_ID,
    question: 'Which API style?',
    options: [],
    epoch: 1,
    runner_id: 'rn_01',
    asked_at: '2026-07-16T11:20:00.000Z',
    answered: false,
  };

  /** Open the chunk page on a chunk parked on `OPEN_QUESTION`, with the answer POST
   * answering `answerResponse`, and submit an answer through the rendered controls. */
  async function answerOnMobile(answerResponse: unknown): Promise<HTMLElement> {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'POST' && path === '/api/questions/qn_77/answers') return answerResponse;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return { ...DETAIL, status: 'waiting_on_human', questions: [OPEN_QUESTION] };
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`, ChunkPage);
    await settle(harness.fixture);
    const el = harness.fixture.nativeElement as HTMLElement;

    // Driven through the mounted ChunkAwaitingHuman's own controls, not the handler
    // directly — the wiring from that reused component up to this page is the point.
    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'graphql';
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    await settle(harness.fixture);
    return el;
  }

  it('renders a lost answer race as an outcome naming the winner, not "Answer failed."', async () => {
    const el = await answerOnMobile(
      stubError(409, {
        won: false,
        question_id: 'qn_77',
        answer: 'rest',
        answered_by: 'alice',
        answered_at: '2026-07-16T11:21:00.000Z',
      }),
    );

    const outcome = el.querySelector('[data-testid="mobile-chunk-action-outcome"]');
    expect(outcome?.textContent).toContain('alice');
    expect(outcome?.textContent).toContain('rest');
    expect(el.querySelector('[data-testid="mobile-chunk-action-error"]')).toBeNull();
  });

  it('still reports a genuine answer failure on the error channel', async () => {
    const el = await answerOnMobile(stubError(404, { detail: 'unknown question qn_77' }));

    expect(el.querySelector('[data-testid="mobile-chunk-action-error"]')?.textContent).toContain('unknown question');
    expect(el.querySelector('[data-testid="mobile-chunk-action-outcome"]')).toBeNull();
  });

  it('shows an answered question’s delivery trail on the phone too', async () => {
    // AC 3 on mobile, which rides on ChunkAwaitingHuman being reused verbatim — pinned
    // here so a future mobile-only fork of the asks region cannot drop it silently.
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return {
        ...DETAIL,
        questions: [
          { ...OPEN_QUESTION, answered: true, answer: 'rest', answered_by: 'alice', delivered: true },
        ],
      };
    });
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="answered-by"]')?.textContent).toContain('alice');
    expect(el.querySelector('[data-testid="answer-delivery"]')?.textContent).toContain('agent resumed');
  });

  it('gives the artifact page a back link to its chunk', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}/artifact/review.findings.2`);

    expect(el.querySelector<HTMLAnchorElement>('[data-testid="mobile-artifact-back"]')?.getAttribute('href')).toBe(
      `/board/chunk/${CHUNK_ID}`,
    );
  });

  // --- The Transcripts tab (blizzard#248 Phase 2) ---------------------------

  it('shows the Transcripts tab option and switches to it, fetching the segment index', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      if (path === `/api/chunks/${CHUNK_ID}/transcripts`) return { chunk_id: CHUNK_ID, segments: [] };
      return DETAIL;
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);

    let el = harness.fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="tab-transcripts"]')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="tab-transcripts"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
    // Non-vacuous (review:F4): with a real `TransitionView` fixture the derivation
    // groups one step from `DETAIL.history` even though the index carries no segments
    // yet, so this renders the tab body, not the `transcripts-empty` alternative.
    expect(el.querySelector('[data-testid="chunk-transcripts-tab"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="transcripts-empty"]')).toBeNull();
    const step = el.querySelector('[data-testid="transcript-step"]');
    expect(step?.textContent).toContain('build · epoch 1');
    expect(step?.textContent).toContain('No segments.');
  });

  it('hides the Transcripts tab option for an identity without transcript:read', async () => {
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return { ...OPERATOR_ME_RESPONSE, permissions: [] };
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return DETAIL;
    });
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="tab-transcripts"]')).toBeNull();
  });

  it('still renders the Transcripts tab’s content on a held deep link, without the tab option in the strip', async () => {
    // A deep link `?tab=transcripts` bypasses the tab strip entirely (the `@switch`
    // renders off the URL, not off which options are visible) — the backend's own 403
    // is what actually gates a viewer-role identity, not this client-side hide (D9).
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return { ...OPERATOR_ME_RESPONSE, permissions: [] };
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      if (path === `/api/chunks/${CHUNK_ID}/transcripts`) return stubError(403, { detail: 'forbidden' });
      return DETAIL;
    });
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=transcripts`);

    expect(el.querySelector('[data-testid="tab-transcripts"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcripts-forbidden"]')?.textContent).toContain('NO PERMISSION');
  });
});

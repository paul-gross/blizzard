import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ChunkPage, ViewportService, type hubApi } from 'fleet';
import {
  assetArtifactView,
  chunkDetail,
  commitArtifactView,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  sidechainSegmentView,
  stubError,
  stubHubClient,
  transcriptSegmentContentView,
  transcriptSegmentIndexEntry,
  transitionView,
  turnSegmentView,
  workItemEntry,
  workRefView,
} from 'fleet/testing';

import { ArtifactPage } from './artifact-page';
import { HUB_CHUNK_PAGE_PROVIDERS } from './hub-chunk-actions';

/** The chunk page's Transcripts tab, mounted as in `chunk-page.spec.ts`. */
const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

const DETAIL: hubApi.ChunkDetail = chunkDetail({
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  open_prs: [],
  closed_prs: [],
  landed_repos: [],
  status: 'running',
  work_refs: [workRefView({ source: 'blizzard', ref: '26', label: null, web_url: null })],
  history: [
    transitionView({
      choice_name: 'pass',
      epoch: 1,
      from_node_id: 'nd_build',
      from_node_name: 'build',
      graph_id: 'gr_1',
      graph_name: 'default',
      recorded_at: '2026-07-16T11:00:00.000Z',
      to_node_id: 'nd_review',
      to_node_name: 'review',
    }),
  ],
  artifacts: [
    assetArtifactView({
      key: 'review.findings.2',
      kind: 'asset',
      name: 'findings',
      node_id: 'nd_review',
      node_name: 'review',
      epoch: 2,
      content: 'THE FINDINGS BODY',
      recorded_at: '2026-07-16T11:30:00.000Z',
    }),
    commitArtifactView({
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
    }),
  ],
});

/** Stands in for the real board page — only its route resolving matters here,
 * so the back link's `/board?chunk=…` navigation actually lands. */
@Component({ selector: 'app-board-stub', template: '' })
class BoardStub {}

const ROUTES = [
  { path: 'board', component: BoardStub },
  { path: 'board/chunk/:chunkId', component: ChunkPage, providers: HUB_CHUNK_PAGE_PROVIDERS },
  { path: 'board/chunk/:chunkId/artifact/:artifactKey', component: ArtifactPage },
];

describe('Chunk page Transcripts tab', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/chunks/{chunk_id}': () => DETAIL,
      'GET /api/chunks/{chunk_id}/work-items': () => ({
        items: [workItemEntry({ source: 'blizzard', ref: '26', label: 'Make the board mobile', web_url: null })],
      }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(ROUTES),
      ],
    });
    TestBed.inject(ViewportService).setOverride('desktop');
  });

  afterEach(() => stub.restore());

  async function open(url: string): Promise<HTMLElement> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return harness.fixture.nativeElement as HTMLElement;
  }


  it('shows the Transcripts tab option and switches to it, fetching the segment index', async () => {
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/chunks/{chunk_id}': () => DETAIL,
      'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
      'GET /api/chunks/{chunk_id}/transcripts': () => ({ chunk_id: CHUNK_ID, segments: [] }),
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
    // Non-vacuous: with a real `TransitionView` fixture the derivation
    // groups one step from `DETAIL.history` even though the index carries no segments
    // yet, so this renders the tab body, not the `transcripts-empty` alternative.
    expect(el.querySelector('[data-testid="chunk-transcripts-tab"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="transcripts-empty"]')).toBeNull();
    const step = el.querySelector('[data-testid="transcript-step"]');
    expect(step?.textContent?.replace(/\s+/g, '')).toContain('build·E1');
    expect(step?.textContent).toContain('No segments.');
  });

  it('hides the Transcripts tab option for an identity without transcript:read', async () => {
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [] }),
      'GET /api/chunks/{chunk_id}': () => DETAIL,
      'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
    });
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="tab-transcripts"]')).toBeNull();
  });

  it('still renders the Transcripts tab’s content on a held deep link, without the tab option in the strip', async () => {
    // A deep link `?tab=transcripts` bypasses the tab strip entirely (the `@switch`
    // renders off the URL, not off which options are visible) — the backend's own 403
    // is what actually gates a viewer-role identity, not this client-side hide.
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [] }),
      'GET /api/chunks/{chunk_id}': () => DETAIL,
      'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
      'GET /api/chunks/{chunk_id}/transcripts': () => stubError(403, { detail: 'forbidden' }),
    });
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=transcripts`);

    expect(el.querySelector('[data-testid="tab-transcripts"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcripts-forbidden"]')?.textContent).toContain('NO PERMISSION');
  });

  it('on mobile, deep-links a sidechain as transcript detail only and clears segment plus sidechain on back', async () => {
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/chunks/{chunk_id}': () => DETAIL,
      'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
      'GET /api/chunks/{chunk_id}/transcripts': () => ({
        chunk_id: CHUNK_ID,
        segments: [
          transcriptSegmentIndexEntry({
            segment_id: 'sg_1', node_id: 'nd_build', epoch: 1, spawn_generation: 0, turn_range_start: 0, turn_range_end: 1,
            final: true, truncated: false, byte_count: 40, normalizer_version: 'v1', harness_version: null,
            received_at: '2026-07-16T11:05:00.000Z',
          }),
        ],
      }),
      'GET /api/chunks/{chunk_id}/transcripts/{segment_id}': () =>
        transcriptSegmentContentView({
          segment_id: 'sg_1', final: true, truncated: false,
          turns: [
            turnSegmentView({
              index: 0, kind: 'sidechain', text: '', timestamp: null, tool: null, thinking_redacted: false, truncated: false,
              sidechain: sidechainSegmentView({
                agent_id: null, agent_type: null, link: 'unlinked',
                turns: [turnSegmentView({ index: 0, kind: 'asst', text: 'sidechain deep link', timestamp: null, tool: null, thinking_redacted: false, truncated: false, sidechain: null })],
              }),
            }),
          ],
        }),
    });
    TestBed.inject(ViewportService).setOverride('mobile');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
    await settle(harness.fixture);
    let el = harness.fixture.nativeElement as HTMLElement;
    const row = el.querySelector<HTMLButtonElement>('[data-testid="transcript-segment-item"]')!;
    row.focus();
    row.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;
    expect(document.activeElement).toBe(el.querySelector('[data-testid="transcript-segment-back"]'));

    const sidechainOpen = el.querySelector<HTMLButtonElement>('[data-testid="transcript-sidechain-open"]')!;
    sidechainOpen.focus();
    sidechainOpen.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;
    expect(document.activeElement).toBe(el.querySelector('[data-testid="transcript-sidechain-back"]'));

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-sidechain-back"]')!.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;
    expect(document.activeElement).toBe(el.querySelector('[data-testid="transcript-segment-back"]'));

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-segment-back"]')?.click();
    await settle(harness.fixture);
    expect(document.activeElement).toBe(el.querySelector('[data-testid="transcript-segment-item"]'));

    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts&segment=sg_1&sidechain=0`);
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="transcripts-tab-nav"]')?.closest('.kmd-pane--hidden')).not.toBeNull();
    expect(el.querySelector('[data-testid="transcript-sidechain-back"]')).not.toBeNull();
    expect(el.textContent).toContain('sidechain deep link');

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-segment-back"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;
    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
    expect(el.querySelector('[data-testid="transcripts-tab-nav"]')?.closest('.kmd-pane--hidden')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-segment-body"]')).toBeNull();
  });
});

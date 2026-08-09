import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

interface Props {
  chunkId?: string;
  history?: unknown[];
  currentNodeId?: string | null;
  currentNodeName?: string | null;
  latestEpoch?: number | null;
  segmentId?: string | null;
  sidechainTurnIndex?: string | null;
}

async function render(
  props: Props,
  route: (method: string, path: string) => unknown,
): Promise<{ el: HTMLElement; fixture: ComponentFixture<ChunkTranscriptsTab> }> {
  stub = stubRequestClient(hubClient, route);
  await TestBed.configureTestingModule({
    imports: [ChunkTranscriptsTab],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkTranscriptsTab);
  fixture.componentRef.setInput('chunkId', props.chunkId ?? 'ch_1');
  fixture.componentRef.setInput('history', props.history ?? []);
  fixture.componentRef.setInput('currentNodeId', props.currentNodeId ?? null);
  fixture.componentRef.setInput('currentNodeName', props.currentNodeName ?? null);
  fixture.componentRef.setInput('latestEpoch', props.latestEpoch ?? null);
  fixture.componentRef.setInput('segmentId', props.segmentId ?? null);
  fixture.componentRef.setInput('sidechainTurnIndex', props.sidechainTurnIndex ?? null);
  // This component is presentational — a URL-held selection, like `ChunkPage` owns for
  // real, is what turns a `pickSegment`/`pickSidechain` output into the next `segmentId`/
  // `sidechainTurnIndex` input. Stand in for that container role here.
  fixture.componentInstance.pickSegment.subscribe((id) => fixture.componentRef.setInput('segmentId', id));
  fixture.componentInstance.pickSidechain.subscribe((idx) => fixture.componentRef.setInput('sidechainTurnIndex', idx));
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

const HISTORY = [
  {
    from_node_id: 'build',
    from_node_name: 'Build',
    to_node_id: 'review',
    to_node_name: 'Review',
    choice_name: 'pass',
    epoch: 1,
    recorded_at: '2026-08-09T00:00:00+00:00',
  },
];

function segment(overrides: Record<string, unknown> = {}) {
  return {
    segment_id: 'seg-1',
    node_id: 'build',
    epoch: 1,
    spawn_generation: 0,
    turn_range_start: 0,
    turn_range_end: 10,
    final: true,
    truncated: false,
    byte_count: 100,
    normalizer_version: 'v1',
    harness_version: null,
    received_at: '2026-08-09T00:00:00+00:00',
    ...overrides,
  };
}

describe('ChunkTranscriptsTab', () => {
  it('lists one group per node-history entry and issues no segment-content request until opened', async () => {
    const { el } = await render({ history: HISTORY }, (method, path) => {
      if (path === '/api/chunks/ch_1/transcripts') return { chunk_id: 'ch_1', segments: [segment()] };
      return {};
    });

    expect(el.querySelectorAll('[data-testid="transcript-step"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="transcript-segment-item"]')).not.toBeNull();
    expect(stub?.forRoute('/api/chunks/ch_1/transcripts/seg-1', 'GET')).toHaveLength(0);
    expect(el.querySelector('[data-testid="transcript-segment-empty"]')?.textContent).toContain('SELECT A SEGMENT');
  });

  it('fetches and renders a segment’s turns once its nav row is clicked', async () => {
    const { el, fixture } = await render({ history: HISTORY }, (method, path) => {
      if (path === '/api/chunks/ch_1/transcripts') return { chunk_id: 'ch_1', segments: [segment()] };
      if (path === '/api/chunks/ch_1/transcripts/seg-1') {
        return {
          segment_id: 'seg-1',
          final: true,
          truncated: false,
          turns: [
            {
              index: 0,
              kind: 'asst',
              timestamp: null,
              text: 'hello from the segment',
              tool: null,
              thinking_redacted: false,
              sidechain: null,
              truncated: false,
            },
          ],
        };
      }
      return {};
    });

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-segment-item"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="transcript-segment-body"]')?.textContent).toContain(
      'hello from the segment',
    );
    expect(stub?.forRoute('/api/chunks/ch_1/transcripts/seg-1', 'GET')).toHaveLength(1);
  });

  it('renders continued-from and continues-in links resolving to each other, for a multi-segment step', async () => {
    const { el, fixture } = await render(
      { history: HISTORY, segmentId: 'seg-2' },
      (method, path) => {
        if (path === '/api/chunks/ch_1/transcripts') {
          return {
            chunk_id: 'ch_1',
            segments: [
              segment({ segment_id: 'seg-1', spawn_generation: 0 }),
              segment({ segment_id: 'seg-2', spawn_generation: 1 }),
            ],
          };
        }
        return { segment_id: 'seg-2', final: true, truncated: false, turns: [] };
      },
    );
    await settle(fixture);

    const back = el.querySelector<HTMLButtonElement>('[data-testid="transcript-continued-from"]');
    expect(back?.textContent).toContain('segment 1');
    expect(el.querySelector('[data-testid="transcript-continues-in"]')).toBeNull();

    back?.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="transcript-continued-from"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-continues-in"]')?.textContent).toContain('segment 2');
  });

  it('renders a truncated segment through KitAsyncState-style banner, never as empty or a generic error', async () => {
    const { el } = await render({ history: HISTORY, segmentId: 'seg-1' }, (method, path) => {
      if (path === '/api/chunks/ch_1/transcripts') return { chunk_id: 'ch_1', segments: [segment({ truncated: true })] };
      return { segment_id: 'seg-1', final: true, truncated: true, turns: [] };
    });

    expect(el.querySelector('[data-testid="transcript-segment-truncated"]')?.textContent).toContain('TRUNCATED');
    expect(el.querySelector('[data-testid="transcript-segment-body"]')).not.toBeNull();
  });

  it('renders the 403 as its own honest state, not a generic error', async () => {
    const { el } = await render({ history: HISTORY }, () => stubError(403, { detail: 'forbidden' }));

    expect(el.querySelector('[data-testid="transcripts-forbidden"]')?.textContent).toContain('NO PERMISSION');
    expect(el.querySelector('[data-testid="transcripts-error"]')).toBeNull();
  });

  it('renders a genuine transport failure distinctly from the permission state', async () => {
    // 404, not 500 — a terminal status per `shouldRetryTranscriptFetch` (unit-tested on
    // its own in `transcript-segments.query.spec.ts`), so this stays fast; a retryable
    // status here would genuinely retry through the real query client for several
    // seconds before settling.
    const { el } = await render({ history: HISTORY }, () => stubError(404, { detail: 'unknown chunk' }));

    expect(el.querySelector('[data-testid="transcripts-error"]')?.textContent).toContain('UNAVAILABLE');
    expect(el.querySelector('[data-testid="transcripts-forbidden"]')).toBeNull();
  });

  it('says so when there are no segments at all', async () => {
    const { el } = await render({}, () => ({ chunk_id: 'ch_1', segments: [] }));

    expect(el.querySelector('[data-testid="transcripts-empty"]')?.textContent).toContain('NO TRANSCRIPT SEGMENTS');
  });

  it('opens an unlinked sidechain standalone and back again, both URL-selectable (D7)', async () => {
    const sidechainTurn = {
      index: 2,
      kind: 'sidechain',
      timestamp: null,
      text: '',
      tool: null,
      thinking_redacted: false,
      sidechain: {
        agent_id: null,
        agent_type: null,
        link: 'unlinked',
        turns: [
          {
            index: 0,
            kind: 'asst',
            timestamp: null,
            text: 'standalone sidechain text',
            tool: null,
            thinking_redacted: false,
            sidechain: null,
            truncated: false,
          },
        ],
      },
      truncated: false,
    };

    const { el, fixture } = await render({ history: HISTORY, segmentId: 'seg-1' }, (method, path) => {
      if (path === '/api/chunks/ch_1/transcripts') return { chunk_id: 'ch_1', segments: [segment()] };
      return { segment_id: 'seg-1', final: true, truncated: false, turns: [sidechainTurn] };
    });

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-sidechain-open"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="transcript-sidechain-back"]')).not.toBeNull();
    expect(el.textContent).toContain('standalone sidechain text');

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-sidechain-back"]')?.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="transcript-sidechain-back"]')).toBeNull();
  });
});

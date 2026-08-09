import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import type { hubApi, KitAsyncStateValue, TranscriptSegmentContentView, TranscriptSegmentIndexEntry } from 'fleet';

import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

interface Props {
  history?: unknown[];
  currentNodeId?: string | null;
  currentNodeName?: string | null;
  latestEpoch?: number | null;
  segments?: readonly TranscriptSegmentIndexEntry[];
  indexState?: KitAsyncStateValue;
  isForbidden?: boolean;
  segmentId?: string | null;
  sidechainTurnIndex?: string | null;
  segmentState?: KitAsyncStateValue;
  segmentData?: TranscriptSegmentContentView;
}

async function render(props: Props): Promise<{ el: HTMLElement; fixture: ComponentFixture<ChunkTranscriptsTab> }> {
  await TestBed.configureTestingModule({
    imports: [ChunkTranscriptsTab],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkTranscriptsTab);
  fixture.componentRef.setInput('history', props.history ?? []);
  fixture.componentRef.setInput('currentNodeId', props.currentNodeId ?? null);
  fixture.componentRef.setInput('currentNodeName', props.currentNodeName ?? null);
  fixture.componentRef.setInput('latestEpoch', props.latestEpoch ?? null);
  fixture.componentRef.setInput('segments', props.segments ?? []);
  fixture.componentRef.setInput('indexState', props.indexState ?? 'ready');
  fixture.componentRef.setInput('isForbidden', props.isForbidden ?? false);
  fixture.componentRef.setInput('segmentId', props.segmentId ?? null);
  fixture.componentRef.setInput('sidechainTurnIndex', props.sidechainTurnIndex ?? null);
  fixture.componentRef.setInput('segmentState', props.segmentState ?? (props.segmentId ? 'ready' : 'empty'));
  fixture.componentRef.setInput('segmentData', props.segmentData);
  // This component is presentational — a URL-held selection, like `ChunkPage` owns for
  // real, is what turns a `pickSegment`/`pickSidechain` output into the next `segmentId`/
  // `sidechainTurnIndex` input, and `ChunkPage`'s own queries into the next `segmentState`/
  // `segmentData` input. Stand in for that container role here.
  fixture.componentInstance.pickSegment.subscribe((id) => fixture.componentRef.setInput('segmentId', id));
  fixture.componentInstance.pickSidechain.subscribe((idx) => fixture.componentRef.setInput('sidechainTurnIndex', idx));
  await fixture.whenStable();
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

function turn(overrides: Partial<hubApi.TurnSegmentViewOutput> = {}): hubApi.TurnSegmentViewOutput {
  return {
    index: 0,
    kind: 'asst',
    timestamp: null,
    text: '',
    tool: null,
    thinking_redacted: false,
    sidechain: null,
    truncated: false,
    ...overrides,
  };
}

function segment(overrides: Partial<TranscriptSegmentIndexEntry> = {}): TranscriptSegmentIndexEntry {
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
  it('lists one group per node-history entry and renders the select-a-segment rest state', async () => {
    const { el } = await render({ history: HISTORY, segments: [segment()] });

    expect(el.querySelectorAll('[data-testid="transcript-step"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="transcript-segment-item"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="transcript-segment-empty"]')?.textContent).toContain('SELECT A SEGMENT');
  });

  it('emits pickSegment when a nav row is clicked, and renders the segment once its data arrives', async () => {
    const { el, fixture } = await render({
      history: HISTORY,
      segments: [segment()],
    });

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-segment-item"]')?.click();
    fixture.componentRef.setInput('segmentState', 'ready');
    fixture.componentRef.setInput('segmentData', {
      segment_id: 'seg-1',
      final: true,
      truncated: false,
      turns: [turn({ text: 'hello from the segment' })],
    });
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="transcript-segment-body"]')?.textContent).toContain(
      'hello from the segment',
    );
  });

  it('renders the loading state while segmentState is loading', async () => {
    const { el } = await render({ history: HISTORY, segments: [segment()], segmentId: 'seg-1', segmentState: 'loading' });

    expect(el.querySelector('[data-testid="transcript-segment-loading"]')).not.toBeNull();
  });

  it('renders continued-from and continues-in links resolving to each other, for a multi-segment step', async () => {
    const { el, fixture } = await render({
      history: HISTORY,
      segments: [segment({ segment_id: 'seg-1', spawn_generation: 0 }), segment({ segment_id: 'seg-2', spawn_generation: 1 })],
      segmentId: 'seg-2',
      segmentData: { segment_id: 'seg-2', final: true, truncated: false, turns: [] },
    });

    const back = el.querySelector<HTMLButtonElement>('[data-testid="transcript-continued-from"]');
    expect(back?.textContent).toContain('segment 1');
    expect(el.querySelector('[data-testid="transcript-continues-in"]')).toBeNull();

    back?.click();
    fixture.componentRef.setInput('segmentData', { segment_id: 'seg-1', final: true, truncated: false, turns: [] });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="transcript-continued-from"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-continues-in"]')?.textContent).toContain('segment 2');
  });

  it('renders a truncated segment through KitAsyncState-style banner, never as empty or a generic error', async () => {
    const { el } = await render({
      history: HISTORY,
      segments: [segment({ truncated: true })],
      segmentId: 'seg-1',
      segmentData: { segment_id: 'seg-1', final: true, truncated: true, turns: [] },
    });

    expect(el.querySelector('[data-testid="transcript-segment-truncated"]')?.textContent).toContain('TRUNCATED');
    expect(el.querySelector('[data-testid="transcript-segment-body"]')).not.toBeNull();
  });

  it('caps a large segment’s rendered turns and says so (review:F7)', async () => {
    const turns: hubApi.TurnSegmentViewOutput[] = Array.from({ length: 1200 }, (_, i) =>
      turn({ index: i, text: `turn ${i}` }),
    );
    const { el } = await render({
      history: HISTORY,
      segments: [segment()],
      segmentId: 'seg-1',
      segmentData: { segment_id: 'seg-1', final: true, truncated: false, turns },
    });

    expect(el.querySelector('[data-testid="transcript-segment-turns-capped"]')?.textContent).toContain('1000');
    expect(el.querySelectorAll('[data-testid="transcript-turn"]')).toHaveLength(1000);
    expect(el.textContent).toContain('turn 1199');
    expect(el.textContent).not.toContain('turn 0');
  });

  it('renders the 403 as its own honest state, not a generic error', async () => {
    const { el } = await render({ history: HISTORY, isForbidden: true });

    expect(el.querySelector('[data-testid="transcripts-forbidden"]')?.textContent).toContain('NO PERMISSION');
    expect(el.querySelector('[data-testid="transcripts-error"]')).toBeNull();
  });

  it('renders a genuine transport failure distinctly from the permission state', async () => {
    const { el } = await render({ history: HISTORY, indexState: 'error' });

    expect(el.querySelector('[data-testid="transcripts-error"]')?.textContent).toContain('UNAVAILABLE');
    expect(el.querySelector('[data-testid="transcripts-forbidden"]')).toBeNull();
  });

  it('renders the loading state while the index read is in flight', async () => {
    const { el } = await render({ history: HISTORY, indexState: 'loading' });

    expect(el.querySelector('[data-testid="transcripts-loading"]')).not.toBeNull();
  });

  it('says so when there are no segments at all', async () => {
    const { el } = await render({});

    expect(el.querySelector('[data-testid="transcripts-empty"]')?.textContent).toContain('NO TRANSCRIPT SEGMENTS');
  });

  it('opens a sidechain standalone and back again, both URL-selectable (D7)', async () => {
    const sidechainTurn = turn({
      index: 2,
      kind: 'sidechain',
      sidechain: {
        agent_id: null,
        agent_type: null,
        link: 'unlinked',
        turns: [turn({ text: 'standalone sidechain text' })],
      },
    });

    const { el, fixture } = await render({
      history: HISTORY,
      segments: [segment()],
      segmentId: 'seg-1',
      segmentData: { segment_id: 'seg-1', final: true, truncated: false, turns: [sidechainTurn] },
    });

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-sidechain-open"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="transcript-sidechain-back"]')).not.toBeNull();
    expect(el.textContent).toContain('standalone sidechain text');

    el.querySelector<HTMLButtonElement>('[data-testid="transcript-sidechain-back"]')?.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="transcript-sidechain-back"]')).toBeNull();
  });

  it('opens a nested sidechain (under a tool call) standalone too, not just an unlinked one (review:F3)', async () => {
    const nestedSidechainTurn = turn({
      index: 1,
      kind: 'tool',
      tool: {
        name: 'Task',
        input: { prompt: 'find X' },
        input_unparsed: null,
        input_shape: 'object',
        tool_use_id: 't1',
        output: 'done',
        output_truncated: false,
      },
      sidechain: {
        agent_id: 'agent-1',
        agent_type: 'explorer',
        link: 'prompt-timestamp',
        turns: [turn({ text: 'nested sidechain text' })],
      },
    });

    const { el, fixture } = await render({
      history: HISTORY,
      segments: [segment()],
      segmentId: 'seg-1',
      segmentData: { segment_id: 'seg-1', final: true, truncated: false, turns: [nestedSidechainTurn] },
    });

    el.querySelector<HTMLButtonElement>(
      '[data-testid="transcript-sidechain-nested"] [data-testid="transcript-sidechain-open"]',
    )?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="transcript-sidechain-back"]')).not.toBeNull();
    expect(el.textContent).toContain('nested sidechain text');
  });
});

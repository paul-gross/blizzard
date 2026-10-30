import { expect, test, type TranscriptSegmentRecord } from '../harness/fixtures';

// The chunk board's Transcripts tab over segments a runner ships: the step's segments list under its unmatched bucket,
// a segment opens with its thinking turn collapsed and its nested sidechain inline under the spawning tool call, the
// continues-in and continued-from links walk between the step's segments, and an unlinked sidechain opens standalone.
// It ships through `fleet`, which refuses below a tenant of its own.
test.use({ isolation: 'fresh-tenant' });

type Turn = TranscriptSegmentRecord['turns'][number];

function turn(fields: Partial<Turn> & Pick<Turn, 'index' | 'kind'>): Turn {
  return { timestamp: null, text: '', tool: null, thinking_redacted: false, sidechain: null, truncated: false, ...fields } as Turn;
}

function segment(
  chunkId: string,
  seq: number,
  spawnGeneration: number,
  range: [number, number],
  turns: Turn[],
): TranscriptSegmentRecord {
  return {
    seq,
    segment_id: `sg_${seq}`,
    chunk_id: chunkId,
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: spawnGeneration,
    turn_range_start: range[0],
    turn_range_end: range[1],
    final: true,
    normalizer_version: 'v1',
    harness_version: 'claude-code-1.0',
    turns,
  };
}

test('test_chunk_transcripts_tab_browser', async ({ fleet, issue, page, wideViewport }) => {
  const { chunk } = await issue('transcript tab browser scenario');
  // The first segment: a thinking turn, and a tool call whose sidechain nests one turn.
  const first = segment(chunk.chunk_id, 1, 1, [0, 2], [
    turn({ index: 0, kind: 'thinking', text: 'weighing two approaches before committing to one' }),
    turn({
      index: 1,
      kind: 'tool',
      tool: {
        name: 'Task',
        input: { prompt: 'survey the callers' },
        input_unparsed: null,
        input_shape: 'object',
        tool_use_id: 't1',
        output: '3 callers found',
        output_truncated: false,
      },
      sidechain: {
        agent_id: 'agent-1',
        agent_type: 'explorer',
        link: 'prompt-timestamp',
        turns: [turn({ index: 0, kind: 'asst', text: 'surveying callers now' })],
      },
    }),
  ]);
  // The second segment continues the first (spawn generation 2) and carries an unlinked sidechain.
  const second = segment(chunk.chunk_id, 2, 2, [2, 3], [
    turn({
      index: 0,
      kind: 'sidechain',
      sidechain: {
        agent_id: null,
        agent_type: null,
        link: 'unlinked',
        turns: [turn({ index: 0, kind: 'asst', text: "an unlinked subagent's own conversation" })],
      },
    }),
  ]);
  const runner = await fleet('fleet-driver');
  const ack = await runner.post('/api/fleet/transcripts', { data: { records: [first, second] } });
  expect(ack.status).toBe(200);
  expect(ack.body.applied).toEqual([1, 2]);

  await page.setViewportSize(wideViewport);
  await page.goto(`/board/chunk/${chunk.chunk_id}`);
  await expect(page.getByTestId('board-chunk-detail')).toBeVisible();

  await page.getByTestId('tab-transcripts').click();
  await expect(page.getByTestId('chunk-transcripts-tab')).toBeVisible();
  // No transition or lease, so both segments' shared `(node_id, epoch)` matches no history row: the unmatched bucket.
  await expect(page.getByTestId('transcript-step')).toHaveCount(1);
  await expect(page.getByTestId('transcript-step')).toContainText('unmatched');
  await expect(page.getByTestId('transcript-segment-item').nth(0)).toContainText('Segment 1');
  await expect(page.getByTestId('transcript-segment-item').nth(1)).toContainText('Segment 2');

  const item = (id: string) => page.locator(`[data-segment-id="${id}"]`).getByTestId('transcript-segment-item');
  await item('sg_1').click();
  await expect(page.getByTestId('transcript-segment-body')).toBeVisible();

  // The thinking turn is collapsed by default.
  const thinking = page.locator('.turn.k-thinking .thinking');
  await thinking.locator('summary').click();
  await expect(thinking.locator('.th-body')).toHaveText('weighing two approaches before committing to one');

  // The nested sidechain sits inline under its tool call; a closed `<details>` still has `textContent`, so open it.
  await page.locator('.turn.k-tool .tool-call').locator('summary').click();
  const nested = page.getByTestId('transcript-sidechain-nested');
  await expect(nested).toBeVisible();
  await expect(nested).toContainText('surveying callers now');

  await expect(page.getByTestId('transcript-continues-in')).toContainText('segment 2');
  await page.getByTestId('transcript-continues-in').click();
  await expect(item('sg_2')).toHaveClass(/\bselected\b/);

  // The unlinked sidechain opens standalone, then back to the segment.
  await page.getByTestId('transcript-sidechain-open').click();
  await expect(page.getByTestId('transcript-sidechain-back')).toBeVisible();
  await expect(page.locator('body')).toContainText("an unlinked subagent's own conversation");
  await page.getByTestId('transcript-sidechain-back').click();
  await expect(page.getByTestId('transcript-sidechain-back')).toHaveCount(0);

  await expect(page.getByTestId('transcript-continued-from')).toContainText('segment 1');
  await page.getByTestId('transcript-continued-from').click();
  await expect(item('sg_1')).toHaveClass(/\bselected\b/);
});

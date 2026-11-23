import type { TranscriptSidechain, TranscriptTool, TranscriptSegmentContent, TranscriptSegmentIndexEntry, TranscriptTurn } from './transcript-turn';

/** One tool call inside a turn, with its output. */
export function toolCallSegmentView(overrides: Partial<TranscriptTool> = {}): TranscriptTool {
  return {
    name: 'Bash',
    tool_use_id: 'toolu_01fixture',
    input: { command: 'git status --short' },
    input_shape: 'object',
    input_unparsed: null,
    output: ' M web/projects/fleet/src/lib/testing/public-api.ts',
    output_truncated: false,
    ...overrides,
  };
}

/** A subagent's nested turns, under the tool call that spawned it. */
export function sidechainSegmentView(overrides: Partial<TranscriptSidechain> = {}): TranscriptSidechain {
  return {
    agent_id: 'ag_01fixture',
    agent_type: 'Explore',
    link: 'toolu_01fixture',
    parent_tool_use_id: 'toolu_01fixture',
    turns: [],
    ...overrides,
  };
}

/** One assistant turn of prose. */
export function turnSegmentView(overrides: Partial<TranscriptTurn> = {}): TranscriptTurn {
  return {
    index: 0,
    kind: 'asst',
    text: 'I will start by reading the work item.',
    timestamp: '2026-01-15T11:58:00.000Z',
    thinking_redacted: false,
    truncated: false,
    tool: null,
    sidechain: null,
    ...overrides,
  };
}

/** A transcript segment's entry in the chunk's segment index. */
export function transcriptSegmentIndexEntry(
  overrides: Partial<TranscriptSegmentIndexEntry> = {},
): TranscriptSegmentIndexEntry {
  return {
    segment_id: 'sg_01fixture0000000000000000000',
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: 1,
    turn_range_start: 0,
    turn_range_end: 1,
    byte_count: 2_048,
    final: true,
    truncated: false,
    harness_id: 'claude-code',
    harness_version: '2.1.0',
    normalizer_version: '1',
    received_at: '2026-01-15T11:59:00.000Z',
    ...overrides,
  };
}

/** A transcript segment's turns. */
export function transcriptSegmentContentView(
  overrides: Partial<TranscriptSegmentContent> = {},
): TranscriptSegmentContent {
  return {
    segment_id: 'sg_01fixture0000000000000000000',
    final: true,
    truncated: false,
    turns: [turnSegmentView()],
    ...overrides,
  };
}

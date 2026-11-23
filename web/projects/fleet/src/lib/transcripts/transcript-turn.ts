/** A normalized tool invocation embedded in a transcript turn. */
export interface TranscriptTool {
  name: string;
  tool_use_id: string | null;
  input: Record<string, unknown>;
  input_shape: string;
  input_unparsed: string | null;
  input_truncated?: boolean;
  output: string | null;
  output_patch?: boolean;
  output_truncated: boolean;
}

/** A nested conversation, possibly linked to its spawning tool. */
export interface TranscriptSidechain {
  agent_id: string | null;
  agent_type: string | null;
  link: string;
  parent_tool_use_id?: string | null;
  turns: TranscriptTurn[];
}

/** A normalized turn. Unknown kinds remain visible to the viewer. */
export interface TranscriptTurn {
  index: number;
  kind: string;
  text: string;
  timestamp: string | null;
  thinking_redacted: boolean;
  truncated: boolean;
  tool: TranscriptTool | null;
  sidechain: TranscriptSidechain | null;
}

/** The contents of one transcript segment. */
export interface TranscriptSegmentContent {
  segment_id: string;
  final: boolean;
  truncated: boolean;
  turns: TranscriptTurn[];
}

/** Metadata for a segment, independent of the daemon serving it. */
export interface TranscriptSegmentIndexEntry {
  segment_id: string;
  node_id: string;
  epoch: number;
  spawn_generation: number;
  turn_range_start: number;
  turn_range_end: number;
  byte_count: number;
  final: boolean;
  truncated: boolean;
  harness_id: string | null;
  harness_version: string | null;
  normalizer_version: string;
  received_at: string;
}

export interface TranscriptSegmentIndex {
  chunk_id: string;
  segments: TranscriptSegmentIndexEntry[];
}

/** The history facts needed to associate a segment with its producing step. */
export interface TranscriptHistoryStep {
  from_node_id: string | null;
  from_node_name?: string | null;
  epoch: number;
}

/** History and current step as used by transcript grouping. */
export interface TranscriptContext {
  history: readonly TranscriptHistoryStep[];
  current: { nodeId: string | null; nodeName: string | null; epoch: number | null; terminal: boolean };
}

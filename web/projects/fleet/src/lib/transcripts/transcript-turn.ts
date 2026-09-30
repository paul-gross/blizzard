/**
 * The shared turn shape {@link TranscriptViewer} (`./transcript-viewer`) renders — a
 * structural type, not a re-export of either generated wire type.
 * The runner's `runnerApi.TurnSegmentView` and the hub's `hubApi.TurnSegmentViewOutput`
 * are two independently-regenerated TS types that nothing else forces to agree; nothing
 * here imports either, so either generated type passes straight through and
 * TypeScript's structural typing accepts it as long as the shapes still match.
 */
export interface TranscriptTool {
  name: string;
  input: Record<string, unknown>;
  input_unparsed: string | null;
  input_shape: string;
  tool_use_id: string | null;
  output: string | null;
  output_truncated: boolean;
  /** This turn carries ONLY a result for the call `tool_use_id` names, shipped in an
   * earlier window — {@link mergeLateLinks} folds it onto that call. */
  output_patch?: boolean;
}

export interface TranscriptSidechain {
  agent_id: string | null;
  agent_type: string | null;
  link: string;
  turns: TranscriptTurn[];
  /** The call that spawned this conversation, when the two shipped in different windows
   * — an id, never an index, which a lease read renumbers. */
  parent_tool_use_id?: string | null;
}

export interface TranscriptTurn {
  index: number;
  kind: string;
  timestamp: string | null;
  text: string;
  tool: TranscriptTool | null;
  thinking_redacted: boolean;
  sidechain: TranscriptSidechain | null;
  truncated: boolean;
}

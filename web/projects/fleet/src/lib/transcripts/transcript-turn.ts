/**
 * The shared turn shape {@link TranscriptViewer} (`./transcript-viewer`) renders — a
 * structural type, not a re-export of either generated wire type (blizzard#248 D10).
 * The runner's `runnerApi.TurnSegmentView` and the hub's `hubApi.TurnSegmentViewOutput`
 * are two independently-regenerated TS types that nothing else forces to agree; nothing
 * here imports either, so a container passes its own generated type straight through
 * and TypeScript's structural typing accepts it as long as the shapes still match.
 * `transcript-turn.spec.ts` pins that assignability — a future divergence between the
 * two OpenAPI schemas fails `web:typecheck` there, not as a runtime render bug.
 */
export interface TranscriptTool {
  name: string;
  input: Record<string, unknown>;
  input_unparsed: string | null;
  input_shape: string;
  tool_use_id: string | null;
  output: string | null;
  output_truncated: boolean;
}

export interface TranscriptSidechain {
  agent_id: string | null;
  agent_type: string | null;
  link: string;
  turns: TranscriptTurn[];
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

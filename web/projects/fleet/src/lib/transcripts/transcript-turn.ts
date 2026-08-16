/**
 * The shared turn shape {@link TranscriptViewer} (`./transcript-viewer`) renders — a
 * structural type, not a re-export of either generated wire type (blizzard#248 D10).
 * The runner's `runnerApi.TurnSegmentView` and the hub's `hubApi.TurnSegmentViewOutput`
 * are two independently-regenerated TS types that nothing else forces to agree; nothing
 * here imports either, so a container passes its own generated type straight through
 * and TypeScript's structural typing accepts it as long as the shapes still match. That
 * assignability is enforced where it actually compiles under `web:typecheck`
 * (`npm run build`, which excludes `*.spec.ts`) — the two real construction sites,
 * `transcript-panel.ts`'s and `chunk-transcripts-tab.ts`'s `[turns]` bindings — not by a
 * dedicated spec (`review:F7`: a prior `transcript-turn.spec.ts` claimed to hold this
 * guard, but specs compile under `tsconfig.spec.json`, which `web:typecheck` never runs).
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
   * earlier window (blizzard#338) — {@link mergeLateLinks} folds it onto that call. */
  output_patch?: boolean;
}

export interface TranscriptSidechain {
  agent_id: string | null;
  agent_type: string | null;
  link: string;
  turns: TranscriptTurn[];
  /** The call that spawned this conversation, when the two shipped in different windows
   * (blizzard#338) — an id, never an index, which a lease read renumbers. */
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

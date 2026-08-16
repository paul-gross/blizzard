import type { TranscriptSidechain, TranscriptTurn } from './transcript-turn';

/**
 * Fold a read's late-linked turns (blizzard#338) back onto the calls they belong to.
 *
 * The runner ships a transcript in windows, so a tool's result and a subagent's conversation
 * routinely arrive in a later record than the call that produced them — carried as an
 * `output_patch` turn and a top-level `sidechain` turn naming `parent_tool_use_id`. Both name
 * their anchor by `tool_use_id`, the one handle that survives a window boundary and the hub's
 * own index renumbering across a lease.
 *
 * Pure and total: a turn whose anchor is not in `turns` — its call shipped in a segment this
 * read does not cover — is passed through as-is rather than dropped, so the content is always
 * visible somewhere even when it cannot be nested.
 */
export function mergeLateLinks(turns: readonly TranscriptTurn[]): TranscriptTurn[] {
  const merged = turns.map((turn) => ({ ...turn }));
  const anchors = new Map<string, TranscriptTurn>();
  for (const turn of merged) {
    const id = turn.tool?.tool_use_id;
    // First wins: a re-ship can put the same call in twice, and the earlier copy is the
    // one every other turn's index ordering already reads against.
    if (id && !turn.tool?.output_patch && !anchors.has(id)) anchors.set(id, turn);
  }

  return merged.filter((turn) => {
    if (turn.tool?.output_patch) return !applyOutput(anchors.get(turn.tool.tool_use_id ?? ''), turn);
    if (turn.kind === 'sidechain' && turn.sidechain?.parent_tool_use_id) {
      return !applySidechain(anchors.get(turn.sidechain.parent_tool_use_id), turn.sidechain);
    }
    return true;
  });
}

/** `true` once `patch`'s output is on `anchor` — the caller then drops the patch turn. */
function applyOutput(anchor: TranscriptTurn | undefined, patch: TranscriptTurn): boolean {
  if (!anchor?.tool || !patch.tool) return false;
  anchor.tool = {
    ...anchor.tool,
    output: patch.tool.output,
    output_truncated: anchor.tool.output_truncated || patch.tool.output_truncated,
  };
  return true;
}

/** `true` once `late` is nested under `anchor` — the caller then drops its top-level turn. */
function applySidechain(anchor: TranscriptTurn | undefined, late: TranscriptSidechain): boolean {
  if (!anchor) return false;
  const held = anchor.sidechain;
  if (held && held.agent_id !== late.agent_id) {
    // The call already carries a DIFFERENT subagent's conversation; nesting this one would
    // overwrite it, so it stays top-level where both remain readable.
    return false;
  }
  // Same agent across several windows: the fragments are one conversation, concatenated in
  // arrival order — which is turn order, since each rides the record that read it.
  anchor.sidechain = held ? { ...held, turns: [...held.turns, ...late.turns] } : { ...late };
  return true;
}

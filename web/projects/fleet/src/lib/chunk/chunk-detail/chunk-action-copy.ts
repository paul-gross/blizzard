/** The claim-vocabulary (bzh:claim-vocabulary) copy table, transcribed for the chunk detail dock's action controls. */

/** One action's copy: its short label, its subtitle (`null` when the action has
 * none), and its full explanatory text. */
export interface ChunkActionCopy {
  readonly label: string;
  readonly subtitle: string | null;
  readonly text: string;
}

/** Pause's copy. `runner` is the claimant's display name, `null` for the still-unclaimed `ready` chunk Pause also
 * reaches, which the base table's `<runner>` slot has nothing to name. */
export function pauseCopy(runner: string | null): ChunkActionCopy {
  return {
    label: 'Pause',
    subtitle: null,
    text: runner
      ? `Parks the agent within ~30s: its worker is interrupted and given a brief grace period to wind down before being force-stopped if it hasn't already exited, but its session is kept. ${runner} keeps the claim and its environment. Resume continues the same session.`
      : `Parks the agent within ~30s: its worker is interrupted and given a brief grace period to wind down before being force-stopped if it hasn't already exited, but its session is kept. No runner has claimed it yet, so pausing just holds it out of the queue. Resume continues the same session.`,
  };
}

/** Resume's copy. `runner` is `null` for a chunk paused before any runner claimed it. */
export function resumeCopy(runner: string | null): ChunkActionCopy {
  return {
    label: 'Resume',
    subtitle: null,
    text: runner
      ? `Clears the pause. ${runner} continues the parked session within ~30s. If the chunk is also waiting on an answer, it stays parked until answered.`
      : `Clears the pause. With no runner holding it yet, clearing the pause simply lets it rejoin the queue. If the chunk is also waiting on an answer, it stays parked until answered.`,
  };
}

/** Detach's copy. Only ever rendered while a live route names a real `runner`/`nodeName`. */
export function detachCopy(runner: string, nodeName: string): ChunkActionCopy {
  return {
    label: `Detach from ${runner}`,
    subtitle: 'End the agent, release the claim',
    text: `Releases ${runner}'s claim: ends the agent's session and releases its environment, so uncommitted work is lost. The chunk stays at node ${nodeName} and returns to READY for any runner to claim, unless an open escalation or question still holds it.`,
  };
}

/** Complete's copy — no interpolation. */
export function completeCopy(): ChunkActionCopy {
  return {
    label: 'Complete…',
    subtitle: 'Mark done, close linked issues',
    text: 'Marks the chunk done and releases its claim, ending any running session. Closes linked GitHub issues as completed, marks hub items delivered, and unblocks chunks that depend on it. Does not merge a PR. Cannot be undone.',
  };
}

/** Delete's copy — no interpolation; any disabled-state subtitle is the caller's. */
export function deleteCopy(): ChunkActionCopy {
  return {
    label: 'Delete…',
    subtitle: 'Remove from the hub permanently',
    text: 'Removes the chunk from the hub and withdraws its hub items. Linked GitHub issues stay open. Refused while another chunk depends on it. Cannot be undone.',
  };
}

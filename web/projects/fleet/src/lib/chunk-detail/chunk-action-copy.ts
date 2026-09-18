/** The claim-vocabulary (bzh:claim-vocabulary) copy table, transcribed for the chunk detail dock's action controls. */

/** One action's copy: its menu label, its menu subtitle (`null` for Pause/Resume, which
 * render as the dock's primary button rather than a menu item), and the full text a
 * `KitTooltip` on the control renders. */
export interface ChunkActionCopy {
  readonly label: string;
  readonly subtitle: string | null;
  readonly text: string;
}

/** Pause's copy. `runnerId` is `null` for the still-unclaimed `ready` chunk Pause also
 * reaches, which the base table's `<runner>` slot has nothing to name. */
export function pauseCopy(runnerId: string | null): ChunkActionCopy {
  return {
    label: 'Pause',
    subtitle: null,
    text: runnerId
      ? `Parks the agent within ~30s: its worker is killed but its session is kept. ${runnerId} keeps the claim and its environment. Resume continues the same session.`
      : `Parks the agent within ~30s: its worker is killed but its session is kept. No runner has claimed it yet, so pausing just holds it out of the queue. Resume continues the same session.`,
  };
}

/** Resume's copy. `runnerId` is `null` for a chunk paused before any runner claimed it. */
export function resumeCopy(runnerId: string | null): ChunkActionCopy {
  return {
    label: 'Resume',
    subtitle: null,
    text: runnerId
      ? `Clears the pause. ${runnerId} continues the parked session within ~30s. If the chunk is also waiting on an answer, it stays parked until answered.`
      : `Clears the pause. With no runner holding it yet, clearing the pause simply lets it rejoin the queue. If the chunk is also waiting on an answer, it stays parked until answered.`,
  };
}

/** Detach's copy. Only ever rendered while a live route names a real `runnerId`/`nodeName`. */
export function detachCopy(runnerId: string, nodeName: string): ChunkActionCopy {
  return {
    label: `Detach from ${runnerId}`,
    subtitle: 'End the agent, release the claim',
    text: `Releases ${runnerId}'s claim: ends the agent's session and releases its environment, so uncommitted work is lost. The chunk stays at node ${nodeName} and returns to READY for any runner to claim, unless an open escalation or question still holds it.`,
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

/** Delete's copy — no interpolation; the disabled-with-dependents subtitle is the
 * header's own computed, layered on top of this at the call site. */
export function deleteCopy(): ChunkActionCopy {
  return {
    label: 'Delete…',
    subtitle: 'Remove from the hub permanently',
    text: 'Removes the chunk from the hub and withdraws its hub items. Linked GitHub issues stay open. Refused while another chunk depends on it. Cannot be undone.',
  };
}

// bzh:frontend-domain-boundary — the one place a routine run's wire is written and its refusals read.
import { type RefusalMap, RunMode } from 'fleet';

import type { RunDraft } from './run.domain';

/** What a run is sent as: the scope, the mode, the trimmed note, and an urgency only when one is chosen. */
export interface RunRequest {
  readonly scopeSlug: string;
  readonly mode: RunMode;
  readonly note: string | null;
  readonly urgency?: string;
}

/** A draft as the run request: a mode other than `delta` is a full run, a blank note is none, and a blank urgency
 * is left to the routine's default. */
export function toRunRequest(draft: RunDraft): RunRequest {
  return {
    scopeSlug: draft.scope,
    mode: draft.mode === 'delta' ? RunMode.DELTA : RunMode.FULL,
    note: draft.note.trim() || null,
    ...(draft.urgency ? { urgency: draft.urgency } : {}),
  };
}

const RUN_REFUSAL_FIELDS: Readonly<Record<string, keyof RunDraft>> = {
  scope_slug: 'scope',
  mode: 'mode',
  urgency: 'urgency',
  note: 'note',
};

/** How the hub's refusals of a run reach the draft: its own fields on theirs, anything else on the form line. */
export const RUN_REFUSALS: RefusalMap<RunDraft> = {
  field: (path) => (Object.hasOwn(RUN_REFUSAL_FIELDS, path) ? RUN_REFUSAL_FIELDS[path] : null),
  kinds: {},
};

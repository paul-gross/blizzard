import type { ConfigChangeView, ConfigChangesPage } from 'fleet';

/** Who last changed a record, and through which door — its newest change row. */
export interface LastChangeVm {
  readonly revision: number;
  readonly at: string;
  readonly actor: string;
  readonly door: string;
}

/** One row of a record's Revisions list. */
export interface RevisionRowVm {
  readonly id: number;
  readonly revision: number;
  readonly op: string;
  /** The fields this revision changed, comma-joined — empty for a change that
   * carries no field diff (a secret's value replace). */
  readonly fields: string;
  readonly at: string;
  readonly actor: string;
  readonly door: string;
}

/** A record's newest change, or `null` before the history resolves or when it is
 * empty. The history read is newest first. */
export function lastChange(history: readonly ConfigChangeView[] | undefined): LastChangeVm | null {
  const newest = history?.[0];
  if (!newest) return null;
  return { revision: newest.revision, at: newest.recorded_at, actor: newest.actor, door: newest.door };
}

/** The Revisions list — one row per change, newest first. */
export function revisionRows(history: readonly ConfigChangeView[] | undefined): readonly RevisionRowVm[] {
  return (history ?? []).map((change) => ({
    id: change.id,
    revision: change.revision,
    op: change.op,
    fields: change.diff.map((field) => field.field).join(', '),
    at: change.recorded_at,
    actor: change.actor,
    door: change.door,
  }));
}

/** Every change across `pages`, in page order — newest first, since each page is. */
export function changesOfPages(pages: readonly ConfigChangesPage[] | undefined): readonly ConfigChangeView[] {
  return (pages ?? []).flatMap((page) => page.changes);
}

/** The key a record's history read filters on — `null` for a built-in record, which
 * carries no revision history, so its read stays at rest. */
export function historyRecordKey(record: { readonly name: string; readonly built_in?: boolean } | null | undefined): string | null {
  if (!record || record.built_in) return null;
  return record.name;
}

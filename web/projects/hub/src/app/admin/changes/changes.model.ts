import type { ConfigChangeView } from 'fleet';

import { recordKindLabel, recordRoute } from '../config-links.model';
import type { ConfigRowVm } from '../config-record-list';

/** One entry of the change log — a single change, or every change one apply wrote. */
export interface ChangeEntry {
  /** The entry's selection key: `apply-<apply_id>` for an apply, else the change id. */
  readonly key: string;
  readonly applyId: string | null;
  readonly actor: string;
  readonly door: string;
  /** When the entry's newest change was recorded. */
  readonly at: string;
  /** The entry's changes, newest first. */
  readonly changes: readonly ConfigChangeView[];
}

/** One change inside an entry's detail. */
export interface ChangeRowVm {
  readonly id: number;
  /** `kind key · op`. */
  readonly title: string;
  /** The changed record's route, or `null` for a kind the board has no surface for. */
  readonly route: readonly string[] | null;
  readonly diff: ConfigChangeView['diff'];
  /** A change with no field diff (a secret's value replace) renders as the revision
   * step it made, `r{n-1} → r{n}`; `null` for a change with a diff. */
  readonly revisionStep: string | null;
}

/** An entry's detail, as its panel renders it. */
export interface ChangeEntryVm {
  readonly title: string;
  readonly actor: string;
  readonly door: string;
  readonly at: string;
  readonly rows: readonly ChangeRowVm[];
}

/**
 * Groups the newest-first change log into entries: rows sharing a non-null
 * `apply_id` are one entry, placed where its newest row falls; every other row is
 * its own entry.
 */
export function changeEntries(changes: readonly ConfigChangeView[]): readonly ChangeEntry[] {
  const entries: { key: string; applyId: string | null; changes: ConfigChangeView[] }[] = [];
  const byApply = new Map<string, ConfigChangeView[]>();
  for (const change of changes) {
    const applyId = change.apply_id ?? null;
    if (applyId !== null) {
      const grouped = byApply.get(applyId);
      if (grouped) {
        grouped.push(change);
        continue;
      }
      const fresh = [change];
      byApply.set(applyId, fresh);
      entries.push({ key: `apply-${applyId}`, applyId, changes: fresh });
    } else {
      entries.push({ key: String(change.id), applyId: null, changes: [change] });
    }
  }
  return entries.map((entry) => ({
    ...entry,
    actor: entry.changes[0].actor,
    door: entry.changes[0].door,
    at: entry.changes[0].recorded_at,
  }));
}

function changeTitle(change: ConfigChangeView): string {
  return `${recordKindLabel(change.record_kind)} ${change.record_key} · ${change.op}`;
}

/** An entry's title — its one change, or how many changes its apply wrote. */
export function entryTitle(entry: ChangeEntry): string {
  if (entry.applyId === null) return changeTitle(entry.changes[0]);
  const count = entry.changes.length;
  return `apply · ${count} change${count === 1 ? '' : 's'}`;
}

/** The change log's rows. */
export function changeEntryRows(entries: readonly ChangeEntry[]): readonly ConfigRowVm[] {
  return entries.map((entry) => ({
    key: entry.key,
    title: entryTitle(entry),
    sub: [entry.actor, `via ${entry.door}`],
    badges: [],
    revision: null,
    retired: false,
  }));
}

/** The entry `key` names among `entries`, or `null`. */
export function entryByKey(entries: readonly ChangeEntry[], key: string | null): ChangeEntry | null {
  if (key === null) return null;
  return entries.find((entry) => entry.key === key) ?? null;
}

/** An entry's detail — `null` with no entry. */
export function changeEntryVm(entry: ChangeEntry | null): ChangeEntryVm | null {
  if (entry === null) return null;
  return {
    title: entryTitle(entry),
    actor: entry.actor,
    door: entry.door,
    at: entry.at,
    rows: entry.changes.map((change) => ({
      id: change.id,
      title: changeTitle(change),
      route: recordRoute(change.record_kind, change.record_key),
      diff: change.diff,
      revisionStep: change.diff.length === 0 ? `r${change.revision - 1} → r${change.revision}` : null,
    })),
  };
}

/** The detail's empty copy — a prompt while nothing is selected, else the selected
 * entry is not among the loaded pages. */
export function changeDetailEmptyText(key: string | null): string {
  return key === null ? 'Pick a change.' : 'This change is not in the loaded log — load older changes to reach it.';
}

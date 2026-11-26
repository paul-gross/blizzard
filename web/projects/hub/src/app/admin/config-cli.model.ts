import type { ViewportMode } from 'fleet';

/** The record kinds the admin surfaces own — the ones a detail here shows a command for. */
export type AdminRecordKind = 'work_source' | 'repository' | 'forge' | 'credential' | 'secret';

/** The CLI verb that changes a record of each admin kind (`contracts/cli/hub.json`). */
const CLI_CHANGE_PATH: Record<AdminRecordKind, string> = {
  work_source: 'blizzard hub source edit',
  repository: 'blizzard hub repo edit',
  forge: 'blizzard hub forge edit',
  credential: 'blizzard hub credential edit',
  secret: 'blizzard hub secret set',
};

/** The command that changes the record `kind` + `name` names — `name` is what the verb takes: a
 * repository's, work source's or credential's slug, a secret's name. */
export function cliChangeCommand(kind: AdminRecordKind, name: string): string {
  return `${CLI_CHANGE_PATH[kind]} ${name}`;
}

/**
 * The command a record detail shows in place of its write controls — on a phone
 * only, where the surfaces are read-only. `null` on a desktop, with no record, and
 * for a built-in record, which no door changes.
 */
export function detailCliCommand(
  mode: ViewportMode,
  kind: AdminRecordKind,
  record: { readonly name: string; readonly built_in?: boolean } | string | null | undefined,
): string | null {
  if (mode !== 'mobile' || !record) return null;
  if (typeof record === 'string') return cliChangeCommand(kind, record);
  return record.built_in ? null : cliChangeCommand(kind, record.name);
}

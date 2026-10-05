import { RecordKind } from 'fleet';
import type { ViewportMode } from 'fleet';

/** The CLI verb that changes a record of each kind (`contracts/cli/hub.json`). */
const CLI_CHANGE_PATH: Record<RecordKind, string> = {
  [RecordKind.WORK_SOURCE]: 'blizzard hub source edit',
  [RecordKind.REPOSITORY]: 'blizzard hub repo edit',
  [RecordKind.SECRET]: 'blizzard hub secret set',
};

/** The command that changes the record `kind` + `name` names. */
export function cliChangeCommand(kind: RecordKind, name: string): string {
  return `${CLI_CHANGE_PATH[kind]} ${name}`;
}

/**
 * The command a record detail shows in place of its write controls — on a phone
 * only, where the surfaces are read-only. `null` on a desktop, with no record, and
 * for a built-in record, which no door changes.
 */
export function detailCliCommand(
  mode: ViewportMode,
  kind: RecordKind,
  record: { readonly name: string; readonly built_in?: boolean } | null | undefined,
): string | null {
  if (mode !== 'mobile' || !record || record.built_in) return null;
  return cliChangeCommand(kind, record.name);
}

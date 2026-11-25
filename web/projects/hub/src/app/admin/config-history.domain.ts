/** Record kinds accepted by the history filter. Response kinds remain open strings. */
export type ConfigRecordKind = 'work_source' | 'secret' | 'repository' | 'forge' | 'credential' | 'scope' | 'routine' | 'cadence' | 'project' | 'project_source_link' | 'project_repository_link' | 'urgency' | 'urgency_order' | 'tenant_settings';

/** A reference may name a record kind newer than this client. */
export interface ConfigRecordRef { readonly kind: string; readonly key: string; readonly label: string | null }

/** A recorded change, preserving the hub's order and diff values and labels. */
export interface ConfigChange {
  readonly id: number;
  readonly revision: number;
  readonly actor: string;
  readonly actor_name: string | null;
  /** The apply that wrote this change, or `null` for a change made on its own. */
  readonly apply_id: string | null;
  readonly door: string;
  readonly op: string;
  readonly recorded_at: string;
  readonly record_kind: string;
  readonly record_key: string;
  readonly record_label: string | null;
  readonly diff: readonly ConfigFieldChange[];
}

export interface ConfigFieldChange {
  readonly field: string;
  readonly old: unknown;
  readonly new: unknown;
  readonly old_label: string | null;
  readonly new_label: string | null;
}

/** One newest-first page of the change log: its changes, the cursor for the next page (`null` at the end), and the
 * total the log's filters select. */
export interface ConfigChangePage {
  readonly changes: readonly ConfigChange[];
  readonly next_before: number | null;
  readonly total: number;
}

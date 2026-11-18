/** A recorded change, preserving the hub's order and diff values and labels. */
export interface ConfigChange {
  readonly id: number;
  readonly revision: number;
  readonly actor: string;
  readonly actor_name: string | null;
  readonly door: string;
  readonly op: string;
  readonly recorded_at: string;
  readonly diff: readonly ConfigFieldChange[];
}

export interface ConfigFieldChange {
  readonly field: string;
  readonly old: unknown;
  readonly new: unknown;
  readonly old_label: string | null;
  readonly new_label: string | null;
}

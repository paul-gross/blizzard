/** One ledger fact a runner recorded. `kind` is the runner's own open word for it, shown as written. */
export interface Fact {
  readonly seq: number;
  readonly kind: string;
  readonly chunk_id: string | null;
  readonly project_id: string | null;
  readonly lease_id: string | null;
  readonly created_at: string;
  readonly acked_at: string | null;
}

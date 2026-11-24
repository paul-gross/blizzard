/** An open question a worker session put to its operator. */
export interface Ask {
  readonly question_id: string;
  readonly chunk_id: string;
  readonly project_id: string | null;
  readonly lease_id: string;
  readonly session_id: string | null;
  readonly harness_id: string | null;
  readonly question: string;
  readonly options: readonly string[];
  readonly asked_at: string;
}

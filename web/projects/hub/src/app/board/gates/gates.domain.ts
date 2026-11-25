/** The open-gate fact needed to mark a board card. */
export interface BoardDecision {
  readonly chunk_id: string;
}

/** An open decision as presented in the board rail. */
export interface BoardDecisionPresentation extends BoardDecision {
  readonly decision_id: string;
  readonly node_name: string;
  readonly imposed_by_runner_id: string | null | undefined;
  readonly imposed_by_runner_name: string | null | undefined;
  readonly choices: readonly string[];
}

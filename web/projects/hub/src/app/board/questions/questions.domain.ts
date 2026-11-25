/** The open question facts needed by the board's glance. */
export interface BoardQuestion {
  readonly chunk_id: string;
  readonly question: string;
}

/** An open question as presented in the board rail. */
export interface BoardQuestionPresentation extends BoardQuestion {
  readonly question_id: string;
  readonly runner_id: string;
  readonly runner_name: string | null | undefined;
  readonly options: readonly string[];
}

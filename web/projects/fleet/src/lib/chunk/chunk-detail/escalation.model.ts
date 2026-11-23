/** The open escalation's commands and cause, as carried by the chunk read. */
export interface ChunkEscalationState {
  readonly cause: string | null;
  readonly detail: string | null;
  readonly epoch: number;
  readonly takeover_command: string;
  readonly wrapped_takeover_command: string;
}

export interface EscalationDetail {
  readonly escalation: ChunkEscalationState | null;
}

/** How an escalation closed — `reason` is the runner's own open word, shown as written. */
export interface EscalationClosure {
  readonly closed_at: string;
  readonly reason: string;
}

/** A lease a worker session escalated to its operator, with the commands that resume or take it over. */
export interface Escalation {
  readonly lease_id: string;
  readonly chunk_id: string;
  readonly project_id: string | null;
  readonly node_id: string;
  readonly epoch: number;
  readonly cause: string | null;
  readonly closed_at: string;
  readonly closure: EscalationClosure | null;
  readonly harness_id: string | null;
  readonly harness_version: string | null;
  readonly model: string | null;
  readonly effort: string | null;
  readonly session_name: string | null;
  readonly resume_command: string;
  readonly wrapped_takeover_command: string | null;
}

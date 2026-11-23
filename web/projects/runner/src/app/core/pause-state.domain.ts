/** A runner's two brakes — its own, set here, and its hub's — and the local brake's reason. */
export interface PauseState {
  readonly local: boolean;
  readonly hub: boolean;
  readonly local_reason: string | null;
}

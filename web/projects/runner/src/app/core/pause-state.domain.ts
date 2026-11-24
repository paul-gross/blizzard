/** A runner's two brakes — its own, set here, and its hub's — the local brake's reason, and whether the runner
 * reads itself held (`effective`, its own judgment over both brakes). */
export interface PauseState {
  readonly local: boolean;
  readonly hub: boolean;
  readonly effective: boolean;
  readonly local_reason: string | null;
}

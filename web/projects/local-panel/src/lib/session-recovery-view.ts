import { ChangeDetectionStrategy, Component, output } from '@angular/core';
import { KitButton } from 'fleet';

/**
 * The runner session-recovery surface (issue #312) — rendered by the runner `App`
 * in place of `<local-panel />` for the one condition {@link SessionRecovery.recovering}
 * covers: a bounce was already attempted and a further no-session `401` arrived
 * before it completed, so there is no session left to render the panel against.
 * Presentational, inputs/outputs only — the container (`App`) owns
 * {@link SessionRecovery} and decides when this renders; retrying re-attempts the
 * bounce, which the container drives through `SessionRecovery.retry()`.
 */
@Component({
  selector: 'local-session-recovery',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './session-recovery-view.html',
  styleUrl: './session-recovery-view.css',
})
export class SessionRecoveryView {
  /** The operator asked to retry — the container clears the mark and re-drives
   * the bounce (`SessionRecovery.retry()`). */
  readonly retry = output<void>();
}

import { ChangeDetectionStrategy, Component, output } from '@angular/core';
import { KitButton } from 'fleet';

/**
 * The runner session-recovery surface — shown in place of the
 * panel when {@link SessionRecovery.recovering} holds: a bounce was already attempted and
 * a further no-session `401` arrived before it completed. Presentational, inputs/outputs
 * only; the `retry` output asks the host to re-attempt the bounce.
 */
@Component({
  selector: 'app-session-recovery',
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

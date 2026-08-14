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
  template: `
    <div class="recovery" data-testid="session-recovery">
      <p class="headline">Session expired</p>
      <p class="detail">The runner could not silently renew its session.</p>
      <fleet-kit-button testid="session-recovery-retry" (click)="retry.emit()">Try again</fleet-kit-button>
    </div>
  `,
  styles: `
    :host {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
    }
    .recovery {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
      padding: 24px 32px;
      border: 1px solid var(--line);
      background: var(--overlay-30);
      font-family: var(--mono);
    }
    .headline {
      font-size: var(--fs-lg);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--amber-hi);
      margin: 0;
    }
    .detail {
      color: var(--label);
      font-size: var(--fs-sm);
      margin: 0;
      text-align: center;
    }
  `,
})
export class SessionRecoveryView {
  /** The operator asked to retry — the container clears the mark and re-drives
   * the bounce (`SessionRecovery.retry()`). */
  readonly retry = output<void>();
}

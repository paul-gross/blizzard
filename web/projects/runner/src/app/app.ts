import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { LocalPanel, SessionRecovery, SessionRecoveryView } from 'local-panel';

/**
 * The runner local-panel app — a thin entrypoint that renders the machine-local
 * panel shell. It composes the shared fleet library (design tokens, and the
 * fleet views as they arrive) plus the runner-only local-panel library.
 *
 * Session-aware fork (issue #312), mirroring the hub shell's own auth fork in
 * spirit though not in shape — `local-panel.ts` is already at the `max-lines`
 * cap, so the fork sits here instead: {@link SessionRecovery.recovering} renders
 * {@link SessionRecoveryView} in place of the panel for the one condition where
 * there is no session left to render it against (a bounce already attempted, and
 * a further no-session `401` arrived before it completed). Every other case —
 * including an upstream `401` the seam cannot fix — renders the panel exactly as
 * before, since only the seam's own classification ever sets `recovering`.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanel, SessionRecoveryView],
  template: `
    @if (recovering()) {
      <local-session-recovery (retry)="onRetry()" />
    } @else {
      <local-panel />
    }
  `,
  styles: `:host { display: block; height: 100%; }`,
})
export class App {
  private readonly sessionRecovery = inject(SessionRecovery);

  protected readonly recovering = this.sessionRecovery.recovering;

  protected onRetry(): void {
    this.sessionRecovery.retry();
  }
}

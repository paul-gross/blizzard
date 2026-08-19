import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { errorMessage, KitBadge, KitButton } from 'fleet';

import { injectLocalPauseMutation, injectRunnerDashboardQuery } from './status.query';

/**
 * The runner top bar's pause/unpause control (issue #133) — the local brake's
 * only mutation surface anywhere in the web UI; the CLI (`blizzard runner
 * pause`/`start`) is the other writer. Rendered in the shared
 * {@link BoardHeader}'s `[header-trailing]` slot beside {@link LocalIdentity}
 * and the header menu — the same composable region `local-panel-layout.ts`
 * already hosts a self-fetching mini-container in (issue #131's design).
 *
 * Reads `GET /api/runner`'s `pause` triad off the same
 * {@link injectRunnerDashboardQuery} every other rail on this panel already
 * polls — no second read. A flip from another session or the spend ceiling
 * still reaches this control live: `PATCH /api/runner` publishes
 * `fact-changed`, which `RUNNER_EVENT_INVALIDATION_REGISTRY` maps to the same
 * dashboard key. This control's own mutation additionally invalidates that
 * key itself, an optimistic same-client shortcut past even the one-frame
 * coalesce window the stream path waits on.
 *
 * The toggle button flips only the **local** brake (`PATCH /api/runner`,
 * through the generated client — `bzh:generated-client`). The hub's own
 * brake (`hub_paused`) is out of scope here (`blizzard hub runner resume` clears
 * it, per the issue) and this control never implies it can touch it: when
 * `hub_paused` is set, a badge says so explicitly, regardless of what the
 * local toggle is doing — an operator whose local brake is off still sees
 * why the runner is not filling, instead of the toggle looking broken. The
 * badge reads `tone="waiting"`, the same tone `chunk-lanes.ts`'s
 * `STATUS_TONE` gives every other `paused` status on the board — not
 * `"needs"`, which would make the identical condition read as an alarm here
 * and a wait everywhere else.
 *
 * A failed PATCH is surfaced, not swallowed — the same "report, don't
 * swallow" convention `chunk-detail.ts`'s pause/resume/detach mutations
 * follow (`bzh:generated-client`'s `onError` + a shared `errorMessage`
 * fold): {@link error} holds the last flip's failure and clears on the next
 * `toggle()`, so an operator whose only mutation surface on this panel
 * fails does not see the toggle silently re-enable with nothing to show
 * for it.
 */
@Component({
  selector: 'local-pause-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, KitButton],
  templateUrl: './local-pause-control.html',
  styleUrl: './local-pause-control.css',
})
export class LocalPauseControl {
  private readonly dashboardQuery = injectRunnerDashboardQuery();
  private readonly pauseMutation = injectLocalPauseMutation();

  /** This runner's own brake — "I won't try". `false` before the first read
   * resolves or on a malformed body, matching {@link LocalInfo}'s guard. */
  protected readonly localPaused = computed<boolean>(() => this.dashboardQuery.data()?.runner?.pause?.local ?? false);

  /** The hub's brake, as last mirrored by PULL — untouched by this control. */
  protected readonly hubPaused = computed<boolean>(() => this.dashboardQuery.data()?.runner?.pause?.hub ?? false);

  /** Disables the toggle while a flip is in flight, so a double click can't
   * race two PATCHes. */
  protected readonly pending = computed<boolean>(() => this.pauseMutation.isPending());

  /** The last flip's failure, or `null` — reset on every new attempt. */
  protected readonly error = signal<string | null>(null);

  protected toggle(): void {
    const next = !this.localPaused();
    this.error.set(null);
    this.pauseMutation.mutate(next, {
      onError: (error) => this.error.set(errorMessage(error, next ? 'Pause failed.' : 'Resume failed.')),
    });
  }
}

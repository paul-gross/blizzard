import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { errorMessage, injectPendingMutationVariables, KitBadge, KitButton } from 'fleet';

import { localPauseMutationKey } from './mutation-keys';
import { injectLocalPauseMutation, injectRunnerDashboardQuery, type LocalPauseVars } from './status.query';

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

  /** This runner's own id — the pause target {@link overridePaused} scopes to,
   * read off the same dashboard read `chunk-detail.ts`'s own `runnerName` reads.
   * `''` before the first read resolves; harmless, since the PATCH itself never
   * carries this id on the wire (only {@link LocalPauseVars}'s local scoping
   * does) and every mutation this component ever fires is scoped identically. */
  private readonly runnerId = computed<string>(() => this.dashboardQuery.data()?.runner?.runner_id ?? '');

  /** Every runner id a local-pause mutation is currently pending for, and its own
   * variables — read through the shared helper (`bzh:frontend-pending-override`)
   * rather than this mutation's own `.isPending()`/`.variables()` alone, the pattern
   * every override site is held to (mirrors `graph-detail.ts`'s
   * `pendingGraphLifecycles`, scoped by `graphId` there and by {@link runnerId} here). */
  private readonly pendingLocalPauses = injectPendingMutationVariables<LocalPauseVars>(localPauseMutationKey);

  /** This runner's brake as it will read once a currently pending flip settles for
   * *this* runner, or `null` while nothing overrides it (`bzh:frontend-pending-
   * override`). Total: this control's own PATCH sets `pause.local` directly and
   * touches nothing else that could outrank it. Purely computed off the mutation's
   * own pending variables, never a cache write, so a rejected flip reverts to the
   * real `pause.local` for free the instant it settles. */
  protected readonly overridePaused = computed<boolean | null>(() => {
    const runnerId = this.runnerId();
    return this.pendingLocalPauses().find((vars) => vars.runnerId === runnerId)?.paused ?? null;
  });

  /** This runner's own brake — "I won't try". `false` before the first read
   * resolves or on a malformed body, matching {@link LocalInfo}'s guard.
   * {@link overridePaused} while a pending flip names one, else the real
   * `pause.local`. */
  protected readonly localPaused = computed<boolean>(() => {
    return this.overridePaused() ?? (this.dashboardQuery.data()?.runner?.pause?.local ?? false);
  });

  /** The hub's brake, as last mirrored by PULL — untouched by this control. */
  protected readonly hubPaused = computed<boolean>(() => this.dashboardQuery.data()?.runner?.pause?.hub ?? false);

  /** The local brake's own reason (blizzard#594) — a usage limit, the spend ceiling, or
   * `null` on a plain manual pause or while nothing overrides it (`overridePaused` names no
   * reason of its own, so a pending flip shows no stale reason until the real read catches
   * up). Read straight off `pause.local_reason`, never derived from `localPaused`'s own
   * override — a reason belongs to the *server's* pause, not an optimistic local one. */
  protected readonly localReason = computed<string | null>(
    () => this.dashboardQuery.data()?.runner?.pause?.local_reason ?? null,
  );

  /** Disables the toggle while a flip is in flight, so a double click can't
   * race two PATCHes. */
  protected readonly pending = computed<boolean>(() => this.pauseMutation.isPending());

  /** The last flip's failure, or `null` — reset on every new attempt. */
  protected readonly error = signal<string | null>(null);

  protected toggle(): void {
    const next = !this.localPaused();
    this.error.set(null);
    this.pauseMutation.mutate(
      { runnerId: this.runnerId(), paused: next },
      { onError: (error) => this.error.set(errorMessage(error, next ? 'Pause failed.' : 'Resume failed.')) },
    );
  }
}

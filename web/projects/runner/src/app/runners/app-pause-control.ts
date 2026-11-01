import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { errorMessage, injectPendingMutationVariables, KitBadge, KitButton, KitErrorLine } from 'fleet';

import { localPauseMutationKey } from '../core/mutation-keys';
import { injectLocalPauseMutation, injectRunnerDashboardQuery, type LocalPauseVars } from '../core/status.query';
import { pendingLocalPause } from './app-pause-control.model';

/**
 * One runner's pause/unpause control. The toggle flips only the **local** brake
 * (`PATCH /runners/{runner_id}/api/runner`); the hub's brake (`hub_paused`) is shown as a badge when
 * set, regardless of the local toggle. Reads the `pause` triad off
 * {@link injectRunnerDashboardQuery}, and its own mutation invalidates that read
 * directly.
 *
 * A failed flip is surfaced, not swallowed: {@link error} holds the last flip's
 * failure and clears on the next `toggle()`.
 */
@Component({
  selector: 'app-pause-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, KitButton, KitErrorLine],
  templateUrl: './app-pause-control.html',
  styleUrl: './app-pause-control.css',
})
export class LocalPauseControl {
  /** The runner this control pauses — the target {@link overridePaused} scopes to. */
  readonly runnerId = input.required<string>();

  /** The toggle alone, where the brakes' state and reason already read beside it. */
  readonly compact = input(false);

  private readonly dashboardQuery = injectRunnerDashboardQuery(() => this.runnerId());
  private readonly pauseMutation = injectLocalPauseMutation(() => this.runnerId());

  /** Every local-pause mutation currently pending for this runner, with its
   * variables (`bzh:frontend-pending-override`). */
  private readonly pendingLocalPauses = injectPendingMutationVariables<LocalPauseVars>(() => localPauseMutationKey(this.runnerId()));

  /** This runner's brake as it will read once a currently pending flip settles for
   * *this* runner, or `null` while nothing overrides it (`bzh:frontend-pending-
   * override`). Total: this control's own PATCH sets `pause.local` directly and
   * touches nothing else that could outrank it. Purely computed off the mutation's
   * own pending variables, never a cache write, so a rejected flip reverts to the
   * real `pause.local` for free the instant it settles. */
  protected readonly overridePaused = computed<boolean | null>(() =>
    pendingLocalPause(this.pendingLocalPauses(), this.runnerId()),
  );

  /** This runner's own brake — "I won't try". {@link overridePaused} while a pending
   * flip names one, else the real `pause.local`; `false` before the first read
   * resolves or on a malformed body. */
  protected readonly localPaused = computed<boolean>(() => {
    return this.overridePaused() ?? (this.dashboardQuery.data()?.runner?.pause?.local ?? false);
  });

  /** The hub's brake, as last mirrored by PULL — untouched by this control. */
  protected readonly hubPaused = computed<boolean>(() => this.dashboardQuery.data()?.runner?.pause?.hub ?? false);

  /** The local brake's own reason — a usage limit, the spend ceiling, or
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

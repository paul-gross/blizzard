import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { KitButton, KitConfirmDialog, KitDialog, RunnerConnection, SubscriptionPaceGroup, WorkspaceTable } from 'fleet';
import type { HostCard } from './host-rows.model';
import { type HostVerbEvent, type HostVerbsVm, NO_HOST_VERBS } from './host-verbs.model';

/** A gesture awaiting the operator's confirm, and the event it emits once confirmed. */
interface PendingConfirm {
  readonly heading: string;
  readonly message: string;
  readonly confirmLabel: string;
  readonly event: HostVerbEvent;
}

/**
 * One host's card on the runners view — its name and last report, its workspaces with
 * their environments in use, and each subscription once naming the runners that spend it.
 * The card's runner rows are projected in by the registry view rendering it, so the rail
 * and the phone each keep their own row markup under one card.
 *
 * It carries the host verbs: retire, revoke-token and enroll on the card, and release on
 * each projected row through {@link askRelease}. Release, retire and revoke-token ask for a
 * danger confirm first; a retire the hub refused asks again, showing the hub's refusal,
 * before it is forced. An enrolled token is shown once, in a dialog, until dismissed.
 *
 * Presentational only: the container folds the hosts read into {@link HostCard} and runs
 * each confirmed {@link HostVerbEvent}.
 */
@Component({
  selector: 'app-host-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitConfirmDialog, KitDialog, SubscriptionPaceGroup, WorkspaceTable],
  templateUrl: './host-card.html',
  styleUrl: './host-card.css',
})
export class HostCardView {
  /** The host's own facts, pre-folded. */
  readonly card = input.required<HostCard>();

  /** The verbs' permission, pending state, refusal and enrolled token. */
  readonly verbs = input<HostVerbsVm>(NO_HOST_VERBS);

  /** A confirmed gesture on this card or one of its rows. */
  readonly verb = output<HostVerbEvent>();

  /** The wire's connection vocabulary, for the host's liveness square. */
  protected readonly connection = RunnerConnection;

  protected readonly pendingConfirm = signal<PendingConfirm | null>(null);

  /** Whether one of this host's own verbs is in flight. */
  protected readonly hostPending = computed(() => this.verbs().pendingHostIds.includes(this.card().hostId));

  /** The hub's refusal of an unforced retire of this host, or `null`. */
  protected readonly refusal = computed(() => {
    const refusal = this.verbs().refusal;
    return refusal?.hostId === this.card().hostId ? refusal.message : null;
  });

  /** The token just enrolled for this host, or `null`. */
  protected readonly enrolledToken = computed(() => {
    const enrolled = this.verbs().enrolled;
    return enrolled?.hostId === this.card().hostId ? enrolled.token : null;
  });

  /** Whether `runnerId`'s release is in flight — the per-row disable. */
  isReleasePending(runnerId: string): boolean {
    return this.verbs().pendingRunnerIds.includes(runnerId);
  }

  /** Ask to release one of the card's runners back to standing alone. */
  askRelease(runnerId: string, runnerName: string): void {
    const hostName = this.card().hostName;
    this.pendingConfirm.set({
      heading: `Release ${runnerName}`,
      message: `Release ${runnerName} from host ${hostName}? It keeps its id and token and stands alone.`,
      confirmLabel: 'Release',
      event: { kind: 'release', hostId: this.card().hostId, runnerId },
    });
  }

  protected askRetire(): void {
    const { hostId, hostName } = this.card();
    this.pendingConfirm.set({
      heading: `Retire host ${hostName}`,
      message: `Retire host ${hostName}? Its token is revoked and every runner under it retired.`,
      confirmLabel: 'Retire',
      event: { kind: 'retire', hostId, force: false },
    });
  }

  protected askRevokeToken(): void {
    const { hostId, hostName } = this.card();
    this.pendingConfirm.set({
      heading: `Revoke token of ${hostName}`,
      message: `Revoke the token of host ${hostName}? It stays added and must be enrolled afresh.`,
      confirmLabel: 'Revoke',
      event: { kind: 'revoke-token', hostId },
    });
  }

  protected enroll(): void {
    this.verb.emit({ kind: 'enroll', hostId: this.card().hostId });
  }

  protected onConfirmed(): void {
    const pending = this.pendingConfirm();
    this.pendingConfirm.set(null);
    if (pending) this.verb.emit(pending.event);
  }

  protected onCancelled(): void {
    this.pendingConfirm.set(null);
  }

  protected forceRetire(): void {
    this.verb.emit({ kind: 'retire', hostId: this.card().hostId, force: true });
  }

  protected dismiss(): void {
    this.verb.emit({ kind: 'dismiss' });
  }
}

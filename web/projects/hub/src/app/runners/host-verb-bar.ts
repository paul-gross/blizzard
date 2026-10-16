import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { KitButton, KitConfirmDialog, KitDialog } from 'fleet';
import { type HostVerbEvent, type HostVerbsVm, NO_HOST_VERBS } from './host-verbs.model';

/** A gesture awaiting the operator's confirm, and the event it emits once confirmed. */
interface PendingConfirm {
  readonly heading: string;
  readonly message: string;
  readonly confirmLabel: string;
  readonly event: HostVerbEvent;
}

/**
 * One host's verbs — Enroll, Revoke token and Retire — with the danger confirms, the
 * forced-retire ask after the hub refused, and the once-shown token dialog. Retire and
 * revoke-token ask for a danger confirm first; a retire the hub refused asks again,
 * showing the hub's refusal, before it is forced. The host card on the rail and the Fleet
 * tab's host detail both render it, so the dialogs have one home.
 *
 * Presentational only: the container runs each confirmed {@link HostVerbEvent}. The bar
 * renders nothing but the dialogs when `verbs().canRetire` is false.
 */
@Component({
  selector: 'app-host-verb-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitConfirmDialog, KitDialog],
  templateUrl: './host-verb-bar.html',
  styleUrl: './host-verb-bar.css',
})
export class HostVerbBar {
  readonly hostId = input.required<string>();
  readonly hostName = input.required<string>();

  /** The verbs' permission, pending state, refusal and enrolled token. */
  readonly verbs = input<HostVerbsVm>(NO_HOST_VERBS);

  /** A confirmed gesture on this host. */
  readonly verb = output<HostVerbEvent>();

  protected readonly pendingConfirm = signal<PendingConfirm | null>(null);

  /** Whether one of this host's own verbs is in flight. */
  protected readonly hostPending = computed(() => this.verbs().pendingHostIds.includes(this.hostId()));

  /** The hub's refusal of an unforced retire of this host, or `null`. */
  protected readonly refusal = computed(() => {
    const refusal = this.verbs().refusal;
    return refusal?.hostId === this.hostId() ? refusal.message : null;
  });

  /** The token just enrolled for this host, or `null`. */
  protected readonly enrolledToken = computed(() => {
    const enrolled = this.verbs().enrolled;
    return enrolled?.hostId === this.hostId() ? enrolled.token : null;
  });

  protected askRetire(): void {
    const hostId = this.hostId();
    const hostName = this.hostName();
    this.pendingConfirm.set({
      heading: `Retire host ${hostName}`,
      message: `Retire host ${hostName}? Its token is revoked and every runner under it retired.`,
      confirmLabel: 'Retire',
      event: { kind: 'retire', hostId, force: false },
    });
  }

  protected askRevokeToken(): void {
    const hostId = this.hostId();
    const hostName = this.hostName();
    this.pendingConfirm.set({
      heading: `Revoke token of ${hostName}`,
      message: `Revoke the token of host ${hostName}? It stays added and must be enrolled afresh.`,
      confirmLabel: 'Revoke',
      event: { kind: 'revoke-token', hostId },
    });
  }

  protected enroll(): void {
    this.verb.emit({ kind: 'enroll', hostId: this.hostId() });
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
    this.verb.emit({ kind: 'retire', hostId: this.hostId(), force: true });
  }

  protected dismiss(): void {
    this.verb.emit({ kind: 'dismiss' });
  }
}

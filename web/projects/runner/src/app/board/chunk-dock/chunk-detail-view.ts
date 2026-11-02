import { ChangeDetectionStrategy, Component, TemplateRef, input, output, signal } from '@angular/core';
import {
  createCopyFlash,
  harnessName,
  KitAsyncState,
  KitBadge,
  KitButton,
  KitConfirmDialog,
  KitErrorLine,
  type KitFact,
  KitFactList,
  type runnerApi,
} from 'fleet';

import { HeartbeatFreshness } from '../../runners/heartbeat-freshness';

/**
 * {@link MachineDetail}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the execution-facts template — the
 * container keeps the `fleet-kit-panel` shell and header projection (which must stay
 * in the template that mounts the panel), the severable `ChunkDetail` read, and the
 * ticking clock {@link heartbeatLabel} is derived from.
 */
@Component({
  selector: 'app-machine-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, KitAsyncState, KitBadge, KitButton, KitConfirmDialog, KitErrorLine, KitFactList],
  templateUrl: './chunk-detail-view.html',
  styleUrl: './chunk-detail-view.css',
})
export class MachineDetailView {
  protected readonly harnessName = harnessName;

  private readonly copyFlash = createCopyFlash();
  protected readonly copied = this.copyFlash.copied;

  /** The chunk's newest attempt, or `null` when nothing is selected — the rest
   * state renders in its place. */
  readonly lease = input<runnerApi.LeaseView | null>(null);

  /** The open escalation for this chunk, when there is one — carries the wrapped takeover
   * command (primary when present) and the raw resume command (the fallback). */
  readonly escalation = input<runnerApi.EscalationView | null>(null);

  /** `-34s` shorthand, or `—` before the first beat / past the skew bound. */
  readonly heartbeatLabel = input('—');

  /** Whether this chunk's requeue is in flight — disables its Requeue control. */
  readonly requeuePending = input(false);

  /** This chunk's last requeue failure, or `null`. */
  readonly requeueError = input<string | null>(null);

  /** Whether this chunk's requeue has landed — its escalation stays listed until the next
   * fill mints a fresh lease, so the notice says what happens next. */
  readonly requeued = input(false);

  /** Emitted with the escalated chunk's id once the operator confirms its requeue. */
  readonly requeue = output<string>();

  protected readonly confirmingRequeue = signal(false);

  /** The execution-facts table's rows — a method, not a stored computed, since the
   * workdir/heartbeat rows need the `<ng-template>`s this template declares for them
   * (`KitFactList`'s own templated-row contract). */
  protected factRows(
    l: runnerApi.LeaseView,
    workdirValue: TemplateRef<unknown>,
    heartbeatValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    return [
      { label: 'session', value: l.session_id ?? '—' },
      { label: 'pid', value: l.pid !== null && l.pid !== undefined ? String(l.pid) : '—' },
      { label: 'env', value: l.environment_id ?? 'released' },
      { label: 'workdir', template: workdirValue },
      { label: 'heartbeat', template: heartbeatValue },
    ];
  }

  /** Copy a takeover command, flashing "Copied" when it lands. */
  protected copy(command: string): void {
    this.copyFlash.copy(command);
  }
}

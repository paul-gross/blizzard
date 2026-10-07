import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { KitBadge, compactRef, type runnerApi } from 'fleet';

import { deriveLeaseStatus } from '../core/lease-status';
import { HeartbeatFreshness } from './heartbeat-freshness';

/**
 * One active lease — presentational, `OnPush`. Shaped like the discovery
 * mock's `.lease` row: compact refs (`L-ZPRR · C-7S5D · epoch 2` —
 * `compactRef`, the app-wide short-name mechanism) with the server-derived
 * `state` right-aligned on the first line as a kit badge, labelled and toned by
 * the kernel's lease fold ({@link deriveLeaseStatus}), `node / env / pid / session` on the
 * second, and a {@link HeartbeatFreshness} bar under both. Deliberately free of
 * issue chips/titles — the lease list is the *liveness* rail; what a chunk is
 * about lives on the machine-chunks list, which carries the work-item enrichment.
 *
 * `data-lease-id` remains a stable hook for the e2e tier to select a row by
 * (`bzh:sweep-release-only-tiers` — `data-*` is the sanctioned e2e seam). The
 * row is the selection affordance: `role="button"` + `tabindex="0"`,
 * click/Enter/Space all emit {@link selectLease}, and `selected` reflects the
 * container's current mark.
 */
@Component({
  selector: 'app-agent-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, KitBadge],
  templateUrl: './agent-row.html',
  styleUrl: './agent-row.css',
})
export class AgentRow {
  /** The lease this row renders, incl. the server-derived `state`. */
  readonly agent = input.required<runnerApi.LeaseView>();

  /** Whether a container considers this row the current selection. */
  readonly selected = input(false);

  /**
   * Emits this row's `lease_id` on click, Enter, or Space. Named
   * `selectLease`, matching `board-shell.ts`'s `selectChunk` — the house
   * convention for a row-select output — rather than the native `select`
   * DOM event name.
   */
  readonly selectLease = output<string>();

  /** Emits {@link selectLease}; `event` is only present for the keyboard bindings, where it is
   * prevented so Space doesn't also scroll the page. */
  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectLease.emit(this.agent().lease_id);
  }

  protected readonly leaseRef = computed(() => compactRef(this.agent().lease_id));
  protected readonly chunkRef = computed(() => compactRef(this.agent().chunk_id));

  protected readonly status = computed(() => deriveLeaseStatus(this.agent()));

  protected readonly isStale = computed(() => this.agent().state === 'stale');
}

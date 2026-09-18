import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';
import { ageMs, compactRef, errorMessage, formatAge, injectNowSignal, KitPanel, KitPanelHeader, type runnerApi } from 'fleet';

import { injectChunkDetailQuery } from './chunk-detail.query';
import { injectChunkPauseMutation } from './chunk-pause.mutations';
import { MachineDetailView } from './chunk-detail-view';
import { injectRunnerDashboardQuery } from './status.query';
import type { MachineChunkStatus } from './chunk-status';
import { MachineDetailHeader } from './machine-detail-header';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so the dock never offers a Pause the server would answer with a 409 — the
 * same table `fleet/chunk-detail/chunk-detail-header.ts`'s own `NOT_PAUSABLE` pins
 * on the hub board's side of this same brake (issue #185). */
const NOT_PAUSABLE = new Set<runnerApi.ChunkStatus>(['done', 'stopped', 'delivering']);

/**
 * The machine detail dock's container (`bzh:frontend-container-presentational`) —
 * the discovery mock's "machine detail" panel for the selected chunk: execution
 * facts *from this box only* (lease, session, pid, env, workdir, heartbeat), and
 * the escalation resume command when one is open. Per-attempt selection and the
 * transcript moved to the runner-local chunk detail route (issue #318) — the
 * chunk name in the header links there now that one exists, a deliberate
 * replacement rather than a regression: it restores the design this dock's own
 * docstring used to describe as deferred ("no cross-view navigation yet").
 *
 * The summary facts, status, and escalation all render off the chunk's newest
 * lease (the last entry of the `leases` list the shell hands in, oldest →
 * newest) — this dock owns no list read of its own.
 *
 * The header ({@link MachineDetailHeader}, a presentational sibling) matches the
 * hub board's own chunk-detail header shape (issue #185, the model at
 * `fleet/chunk-detail/chunk-detail-header.ts`). Unlike the rest of this dock's
 * facts (container-folded, "one owner"), the work-item links and the pause fact
 * are this dock's own severable enrichment — the same self-fetching shape
 * `injectChunkTitleQuery` already established for the chunks list — read through
 * {@link injectChunkDetailQuery}, the runner's pass-through proxy serving the
 * hub's `ChunkDetail` aggregate whole. `pause` is the *only*
 * way this panel learns a chunk is paused (it sits independently of the derived
 * {@link status}, which folds in machine-only facts the hub aggregate does not
 * carry), so Pause/Resume's own gating reads the fresh `pause`/`status` off that
 * read, never the machine-derived one.
 *
 * The execution-facts template itself belongs to the presentational
 * {@link MachineDetailView} (`bzh:frontend-container-presentational`) — this
 * container keeps only what `fleet-kit-panel`'s header-slot projection requires
 * of the template that mounts the panel, plus the query and the ticking clock.
 *
 * The dock paints its own panel chrome via {@link KitPanel} (issue #307) — the
 * same bezel/background every sibling region in `local-panel-layout.ts` wears —
 * rather than mounting bare. `KitPanel`'s header slot can only be filled
 * from the template that mounts the panel, so this container is the one place
 * that projection can happen; `MachineDetailHeader` is projected in with
 * `KitPanel`'s own `label` left unset, so exactly one header bar renders —
 * {@link MachineDetailHeader}'s own — rather than stacking below a second,
 * empty one.
 */
@Component({
  selector: 'local-machine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, KitPanelHeader, MachineDetailHeader, MachineDetailView],
  templateUrl: './chunk-detail.html',
  styleUrl: './chunk-detail.css',
})
export class MachineDetail {
  /** The selected chunk's attempts, oldest → newest; empty when nothing is
   * selected. The newest is the summary/status subject. */
  readonly leases = input.required<readonly runnerApi.LeaseView[]>();

  /** The derived machine-side status for the selected chunk (shell-folded). */
  readonly status = input<MachineChunkStatus | null>(null);

  /** The open escalation for this chunk, when there is one — carries the resume command. */
  readonly escalation = input<runnerApi.EscalationView | null>(null);

  /** The chunk's newest attempt (the `leases` list's last entry) — the summary,
   * status, and escalation all render off it. */
  protected readonly newestLease = computed<runnerApi.LeaseView | null>(() => this.leases().at(-1) ?? null);

  /** Emitted when the operator dismisses the dock (issue #185) — the container
   * clears the selection, mirroring the hub header's own `dismiss`. */
  readonly dismiss = output<void>();

  /** The selected chunk's id — the severable detail read's subject. */
  protected readonly chunkId = computed<string | null>(() => this.newestLease()?.chunk_id ?? null);

  /**
   * The dock's own severable enrichment (issue #185) — the `ChunkDetail` read,
   * not container-folded: work-item links and the pause fact reach the header through
   * this, the same self-fetching shape `injectChunkTitleQuery` established for the
   * chunks list (`chunk-title.query.ts`, `chunk-row.ts`).
   */
  protected readonly detailQuery = injectChunkDetailQuery(() => this.chunkId());

  /** The chunk's work refs, for the header — mirrors the hub header's own `pointers`. */
  protected readonly workRefs = computed<readonly runnerApi.WorkRefView[]>(
    () => this.detailQuery.data()?.work_refs ?? [],
  );

  /** The chunk's open operator pause, if any — read off the fresh
   * `ChunkDetail.pause`, never the machine-derived {@link status}, which folds
   * in facts the hub aggregate does not carry (mirrors the hub header's own `pause`). */
  protected readonly pause = computed<runnerApi.PauseView | null>(() => this.detailQuery.data()?.pause ?? null);

  /** Whether an **unpaused** chunk may be paused — mirrors the hub `PauseService`'s
   * refusal so the header never offers a control the server would answer with a 409.
   * `undefined` (the read hasn't resolved yet) degrades to not-pausable, so no button
   * flashes before the fresh state is known. */
  protected readonly pausable = computed<boolean>(() => {
    const s = this.detailQuery.data()?.status;
    return s !== undefined && !NOT_PAUSABLE.has(s);
  });

  /** The header's Pause/Resume mutation — fired from its `pauseChunk`/`resumeChunk`
   * outputs, once the operator has already confirmed. */
  protected readonly pauseMutation = injectChunkPauseMutation();

  /** Whether the pause/resume mutation is in flight — read straight off the mutation's
   * own `.isPending()` and threaded to the header's Pause/Resume buttons, so a double
   * click cannot fire the request twice while the first still settles. This dock shows
   * exactly one chunk at a time, so there is no sibling row to distinguish pending
   * mutations by variables (mirrors `fleet/chunk-detail/chunk-detail.ts`'s own
   * `pausePending`). */
  protected readonly pausePending = computed<boolean>(() => this.pauseMutation.isPending());

  /** The dock's last Pause/Resume failure, or `null` — reset on every new attempt
   * (issue #46's "report, don't swallow" requirement, the same shape
   * {@link LocalPauseControl}'s own `error` follows for the top bar's pause toggle),
   * and whenever a different chunk is selected (mirrors `fleet/chunk-detail/
   * chunk-detail.ts`'s own `beginAction`, below), so a stale failure from a chunk
   * no longer open never lingers into the next one's dock. */
  protected readonly actionError = signal<string | null>(null);

  constructor() {
    effect(() => {
      this.chunkId();
      this.actionError.set(null);
    });
  }

  /** This runner's own id, for the header's `pauseCopy`/`resumeCopy` `<runner>` slot
   * (`bzh:claim-vocabulary`) — the same dashboard read every other rail on the panel
   * already polls (`status.query.ts`'s own dedupe note), not a second one. */
  private readonly dashboardQuery = injectRunnerDashboardQuery();
  protected readonly runnerName = computed<string | null>(() => this.dashboardQuery.data()?.runner?.runner_id ?? null);

  protected readonly leaseRef = computed(() => {
    const l = this.newestLease();
    return l ? compactRef(l.lease_id) : '';
  });

  /** Ticks once a second so {@link heartbeatLabel} advances between polls, the
   * same cadence `HeartbeatFreshness`'s own bar reads (`bzh:frontend-formatters`). */
  private readonly now = injectNowSignal(1000);

  /**
   * `-34s` shorthand, or `—` before the first beat / past the skew bound —
   * decoration only; the server-derived state carries liveness (`bzh:utc-instants`).
   */
  protected readonly heartbeatLabel = computed<string>(() => {
    const l = this.newestLease();
    if (!l || l.state === 'closed') return '—';
    const age = ageMs(l.last_heartbeat_at, this.now());
    return age === null ? '—' : formatAge(age);
  });

  /** Pause the given chunk — the header's `pauseChunk` output, once the operator has
   * already confirmed. Mirrors `fleet/chunk-detail/chunk-detail.ts`'s own `onPause`: a
   * refusal (already paused, a status the hub's `PauseService` won't pause) is reported
   * on {@link actionError} rather than swallowed. */
  protected onPause(chunkId: string): void {
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { chunkId, paused: true },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Pause failed.')) },
    );
  }

  /** Resume the given chunk — the header's `resumeChunk` output, once the operator has
   * already confirmed. Mirrors `fleet/chunk-detail/chunk-detail.ts`'s own `onResume`. */
  protected onResume(chunkId: string): void {
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { chunkId, paused: false },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Resume failed.')) },
    );
  }
}

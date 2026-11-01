import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';
import {
  type DaemonTarget,
  errorMessage,
  injectChunkDetailQuery,
  injectNowSignal,
  KitPanel,
  KitPanelHeader,
  type runnerApi,
  KitErrorLine,
} from 'fleet';

import { injectChunkPauseMutation } from './chunk-pause.mutations';
import { heartbeatLabel, liveWorker, runnerTarget } from './chunk-detail.model';
import { injectPanelProjects } from '../../core/panel-reads';
import { MachineDetailView } from './chunk-detail-view';
import { type PanelLease, projectNameFor } from '../../core/panel-reads.model';
import type { MachineChunkStatus } from '../../core/chunk-status';
import { MachineDetailHeader } from './machine-detail-header';

/**
 * The machine detail dock's container (`bzh:frontend-container-presentational`) —
 * the discovery mock's "machine detail" panel for the selected chunk: execution
 * facts *from this box only* (its worker, session, pid, env, workdir, heartbeat), and
 * the escalation resume command when one is open. Per-attempt selection and the
 * transcript belong to the runner-local chunk detail route, which the chunk name in
 * the header links to.
 *
 * The summary facts, status, and escalation all render off the chunk's newest
 * lease (the last entry of the `leases` list the shell hands in, oldest →
 * newest) — this dock owns no list read of its own. Every read and action targets that
 * lease's own runner.
 *
 * The header ({@link MachineDetailHeader}) is a presentational sibling. The
 * work-item links and the pause fact are this dock's own self-fetched enrichment
 * ({@link injectChunkDetailQuery}); Pause/Resume's gating reads the fresh
 * `pause`/`status` off that read, never the machine-derived {@link status}.
 *
 * The execution-facts template itself belongs to the presentational
 * {@link MachineDetailView} (`bzh:frontend-container-presentational`) — this
 * container keeps only what `fleet-kit-panel`'s header-slot projection requires
 * of the template that mounts the panel, plus the query and the ticking clock.
 *
 * The dock paints its own panel chrome via {@link KitPanel}, with
 * {@link MachineDetailHeader} projected in and `label` left unset.
 */
@Component({
  selector: 'app-machine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, KitPanelHeader, MachineDetailHeader, MachineDetailView, KitErrorLine],
  templateUrl: './chunk-detail.html',
  styleUrl: './chunk-detail.css',
})
export class MachineDetail {
  /** The selected chunk's attempts, oldest → newest; empty when nothing is
   * selected. The newest is the summary/status subject. */
  readonly leases = input.required<readonly PanelLease[]>();

  /** The derived machine-side status for the selected chunk (shell-folded). */
  readonly status = input<MachineChunkStatus | null>(null);

  /** The open escalation for this chunk, when there is one — carries the resume command. */
  readonly escalation = input<runnerApi.EscalationView | null>(null);

  /** The chunk's newest attempt (the `leases` list's last entry) — the summary,
   * status, and escalation all render off it. */
  protected readonly newestLease = computed<PanelLease | null>(() => this.leases().at(-1) ?? null);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** The selected chunk's id — the severable detail read's subject. */
  protected readonly chunkId = computed<string | null>(() => this.newestLease()?.chunk_id ?? null);

  /**
   * The dock's own severable enrichment — the `ChunkDetail` read,
   * not container-folded: work-item links and the pause fact reach the header through
   * this, the same self-fetching shape `injectChunkTitleQuery` established for the
   * chunks list (`chunk-title.query.ts`, `chunk-row.ts`).
   */
  private readonly runnerId = computed<string | null>(() => this.newestLease()?.owner.runnerId ?? null);
  private readonly target = computed<DaemonTarget | null>(() => runnerTarget(this.runnerId()));

  /** The chunk detail route's path segments before the chunk id. */
  protected readonly linkBase = ['/board', 'chunk'];

  protected readonly detailQuery = injectChunkDetailQuery(
    () => this.target(),
    () => this.chunkId(),
  );

  /** The chunk's work refs, for the header — mirrors the hub header's own `pointers`. */
  protected readonly workRefs = computed<readonly runnerApi.WorkRefView[]>(
    () => this.detailQuery.data()?.work_refs ?? [],
  );

  /** The chunk's open operator pause, if any — read off the fresh
   * `ChunkDetail.pause`, never the machine-derived {@link status}, which folds
   * in facts the hub aggregate does not carry (mirrors the hub header's own `pause`). */
  protected readonly pause = computed<runnerApi.PauseView | null>(() => this.detailQuery.data()?.pause ?? null);

  /** Whether an **unpaused** chunk may be paused — the fresh `ChunkDetail.pausable`, so the
   * header never offers a control the server would answer with a 409. An unresolved read
   * degrades to not-pausable, so no button flashes before the fresh state is known. */
  protected readonly pausable = computed<boolean>(() => this.detailQuery.data()?.pausable ?? false);

  /** The header's Pause/Resume mutation — fired from its `pauseChunk`/`resumeChunk`
   * outputs, once the operator has already confirmed. */
  protected readonly pauseMutation = injectChunkPauseMutation(() => this.runnerId());

  /** Whether the pause/resume mutation is in flight — read straight off the mutation's
   * own `.isPending()` and threaded to the header's Pause/Resume buttons, so a double
   * click cannot fire the request twice while the first still settles. This dock shows
   * exactly one chunk at a time, so there is no sibling row to distinguish pending
   * mutations by variables. */
  protected readonly pausePending = computed<boolean>(() => this.pauseMutation.isPending());

  /** The dock's last Pause/Resume failure, or `null` — reset on every new attempt
   * and whenever a different chunk is selected,
   * so a stale failure never lingers into the next chunk's dock. */
  protected readonly actionError = signal<string | null>(null);

  constructor() {
    effect(() => {
      this.chunkId();
      this.actionError.set(null);
    });
  }

  /** The chunk's runner's bare name, for the header's
   * `pauseCopy`/`resumeCopy` `<runner>` slot (`bzh:claim-vocabulary`). */
  protected readonly runnerName = computed<string | null>(() => this.newestLease()?.owner.label ?? null);

  /** The chunk's live worker — its newest lease while that has not ended — for the header. */
  protected readonly worker = computed<PanelLease | null>(() => liveWorker(this.newestLease()));

  private readonly projects = injectPanelProjects();

  /** The chunk's project by name, for the header's identity line. */
  protected readonly projectName = computed<string | null>(() =>
    projectNameFor(this.projects(), this.newestLease()?.projectSlug ?? null),
  );

  /** Ticks once a second so {@link heartbeatLabel} advances between polls, the
   * same cadence `HeartbeatFreshness`'s own bar reads (`bzh:frontend-formatters`). */
  private readonly now = injectNowSignal();

  /** `-34s` since the newest lease's last beat, or `—` — decoration only; the
   * server-derived state carries liveness (`bzh:utc-instants`). */
  protected readonly heartbeatLabel = computed<string>(() => heartbeatLabel(this.newestLease(), this.now()));

  /** Pause the given chunk — the header's `pauseChunk` output, once the operator has
   * already confirmed. Mirrors the hub's `board/chunk-dock/chunk-detail.ts`'s own `onPause`: a
   * refusal (already paused, a status the hub's `PauseService` won't pause) is reported
   * on {@link actionError} rather than swallowed. */
  protected onPause(chunkId: string): void {
    const runnerId = this.runnerId();
    if (runnerId === null) return;
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { runnerId, chunkId, paused: true },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Pause failed.')) },
    );
  }

  /** Resume the given chunk — the header's `resumeChunk` output, once the operator has
   * already confirmed. Mirrors the hub's `board/chunk-dock/chunk-detail.ts`'s own `onResume`. */
  protected onResume(chunkId: string): void {
    const runnerId = this.runnerId();
    if (runnerId === null) return;
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { runnerId, chunkId, paused: false },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Resume failed.')) },
    );
  }
}

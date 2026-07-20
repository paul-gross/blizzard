import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import {
  FleetLiveUpdates,
  STATUS_TONE,
  compactRef,
  injectHubChunksQuery,
  injectHubFleetSpendQuery,
  injectHubHealthQuery,
  injectHubQuestionsQuery,
  injectHubRunnersQuery,
  type ChunkSummary,
} from 'fleet';

import { startOfLocalDayIso } from '../../local-day';
import { GlanceView, type AttentionRow, type DoneRow, type MotionRow, type Vitals } from './glance-view';

/**
 * The mobile glance board (mock screen C, `../docs/designs/mobile/core-flows.html`)
 * — a container (`bzh:frontend-container-presentational`): it owns every read the
 * shell needs and folds them into the attention-ordered buckets the
 * presentational {@link GlanceView} renders, so "does anything need me?" answers
 * in one scroll rather than a three-column scan.
 *
 * A routed page, not a branch inside {@link BoardPage}: the hub's route table
 * (`app.routes.ts`) mounts this component at `board` guarded by `fleet`'s
 * `matchesMobileViewport`, with `BoardPage`'s own unguarded `board` entry as the
 * desktop fallback — the same URL, two shells, the fork made once in the route
 * table rather than per-page (see `app.routes.ts`'s doc comment).
 *
 * Every number and row here comes from queries {@link BoardPage}'s desktop shell
 * already reads — `injectHubChunksQuery`, `injectHubQuestionsQuery`,
 * `injectHubRunnersQuery`, `injectHubHealthQuery`, `injectHubFleetSpendQuery` —
 * plus the same `FleetLiveUpdates` spine the app root starts: no new backend
 * plumbing, per the mobile README's shared-guts inventory ("Reuses chunks.query
 * … status vocabulary from chunk-lanes. New: attention-sort, vitals strip,
 * mobile board shell").
 *
 * `ChunkSummary` carries no per-row instant — no started-at or landed-at
 * timestamp, only `ChunkDetail`'s transition history does, and fetching that per
 * row would turn this glance read into an N+1 the desktop board doesn't pay
 * either. The mock's "age"/"landed time" cells are therefore not rendered here;
 * the section a chunk sorts into (and its status pill) carries that signal
 * instead of a fabricated instant.
 */
@Component({
  selector: 'app-glance-board',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GlanceView],
  template: `
    <app-glance-view
      [vitals]="vitals()"
      [needsYou]="needsYou()"
      [inMotion]="inMotion()"
      [doneToday]="doneToday()"
      [spend]="spendToday.data() ?? null"
    />
  `,
  // A routed page fills the router outlet area, same as `BoardPage`'s own
  // `:host` — `GlanceView`'s `:host { height: 100% }` needs this to resolve
  // against, and `App`'s flex-column layout needs `flex: 1` to give this
  // page the remaining space rather than its content's intrinsic size.
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
  `,
})
export class GlanceBoard {
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly questionsQuery = injectHubQuestionsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly health = injectHubHealthQuery();
  private readonly live = inject(FleetLiveUpdates);

  /** The fleet-wide spend-since read (issue #60) — the same local-midnight
   * window the titlebar's own cell reads (`startOfLocalDayIso`), so the two
   * never disagree and share one query-cache entry. */
  protected readonly spendToday = injectHubFleetSpendQuery(() => startOfLocalDayIso());

  private readonly chunks = computed<readonly ChunkSummary[]>(() => this.chunksQuery.data() ?? []);
  private readonly questions = computed(() => this.questionsQuery.data() ?? []);
  private readonly runners = computed(() => this.runnersQuery.data() ?? []);

  /**
   * Open asks first (the more specific "why"), then any chunk in a
   * human-attention tone (`chunk-lanes.ts`'s `STATUS_TONE` — `waiting` or
   * `needs`) an open ask hasn't already covered — folded into one
   * attention-ordered list (the mock's "Needs you"), deduped by chunk id so a
   * parked chunk with an open ask shows once, not twice.
   */
  protected readonly needsYou = computed<readonly AttentionRow[]>(() => {
    const rows = new Map<string, AttentionRow>();
    for (const question of this.questions()) {
      rows.set(question.chunk_id, {
        chunkId: question.chunk_id,
        shortId: compactRef(question.chunk_id),
        runnerId: question.runner_id,
        tone: 'waiting',
        pillLabel: 'ask',
        sub: question.question,
      });
    }
    for (const chunk of this.chunks()) {
      if (rows.has(chunk.chunk_id)) continue;
      const tone = STATUS_TONE[chunk.status];
      if (tone !== 'needs' && tone !== 'waiting') continue;
      rows.set(chunk.chunk_id, {
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        runnerId: chunk.runner_id ?? null,
        tone,
        pillLabel: tone === 'needs' ? 'needs human' : 'waiting',
        sub: chunk.current_node_name ?? chunk.current_node_id ?? '—',
      });
    }
    return [...rows.values()];
  });

  /** Chunks whose tone is `running` (`STATUS_TONE`'s running lane: `running` +
   * `delivering`) — the mock's "In motion". */
  protected readonly inMotion = computed<readonly MotionRow[]>(() =>
    this.chunks()
      .filter((chunk) => STATUS_TONE[chunk.status] === 'running')
      .map((chunk) => ({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        runnerId: chunk.runner_id ?? null,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        pillLabel: chunk.status === 'delivering' ? ('deliver' as const) : ('run' as const),
        costUsd: chunk.cost?.cost_usd ?? 0,
        costPartial: chunk.cost?.cost_partial ?? false,
      })),
  );

  /** Chunks whose tone is `done` (`stopped`/`done`) — the mock's "Done today". */
  protected readonly doneToday = computed<readonly DoneRow[]>(() =>
    this.chunks()
      .filter((chunk) => STATUS_TONE[chunk.status] === 'done')
      .map((chunk) => ({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        // Only labeled pointers show, same rule as the desktop board's card (board-shell.ts).
        pointerLabel: (chunk.pm_pointers ?? []).flatMap((p) => (p.label ? [p.label] : [])).join(' '),
      })),
  );

  /** The vitals strip's four numbers: the two attention/motion counts above,
   * the fleet registry's online fraction, and the live spine's connection —
   * the same connection-state fold `App`'s titlebar reads (`app.ts`). */
  protected readonly vitals = computed<Vitals>(() => {
    const runners = this.runners();
    const online = runners.filter((runner) => runner.online).length;
    const streamState = this.live.status();
    const liveLabel =
      streamState === 'open'
        ? 'live'
        : streamState === 'reconnecting'
          ? 'reconnecting'
          : this.health.isError()
            ? 'offline'
            : 'connecting';
    return {
      needsYou: this.needsYou().length,
      running: this.inMotion().length,
      runnersUpLabel: `${online}/${runners.length}`,
      live: streamState === 'open',
      liveLabel,
    };
  });
}

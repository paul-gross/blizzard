import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { type KitAsyncStateValue, FleetLiveUpdates, type LoggedEvent } from 'fleet';
import { ACTIVITY_LIMIT, injectHubActivityQuery } from './activity.query';
import { ActivityFeedView, type ActivityRow } from './activity-view';
import { activityPanelState, activityRows, backfillEvents, mergeActivityFeeds } from './activity-panel.model';

/** The rendered-row cap for the merged backfill + live feed — the backfill read's own limit. */
const RENDER_LIMIT = ACTIVITY_LIMIT;

/**
 * The Activity feed panel's **container** (`bzh:frontend-container-presentational`).
 *
 * Owns two independent reads of the same underlying feed and merges them into one
 * rendered list:
 *
 * - The **live** tee: {@link FleetLiveUpdates}'s bounded SSE ring, the bridge from
 *   transport to query cache and the destination for the broker's connect-time replay.
 * - The **backfill**: {@link injectHubActivityQuery}, a one-shot `GET /api/activity`
 *   read on mount, so the feed shows recent history immediately rather than starting
 *   empty and filling in only as new frames arrive.
 *
 * The two are merged in {@link merged}: a backfilled row and a live frame naming the
 * same `key` must render as exactly one row, preferring the live
 * copy (it may carry more current info) — so the merge drops a backfilled row whose
 * `key` also names a live frame already present, never the other way around. A row
 * with no `key` at all can't collide with anything and always renders standalone. The
 * merged list is newest-first-capped at {@link RENDER_LIMIT} by sorting on `at`, not by
 * trusting either source's own ordering (the backfill arrives newest-first over the
 * wire; the live ring is oldest-first) — sorting once here is one rule instead of two
 * assumptions to keep in sync.
 */
@Component({
  selector: 'app-activity-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ActivityFeedView],
  templateUrl: './activity-panel.html',
})
export class ActivityPanel {
  private readonly live = inject(FleetLiveUpdates);
  protected readonly activityQuery = injectHubActivityQuery();

  /** The backfill read shaped into {@link LoggedEvent}s, oldest-assignment-order
   * irrelevant (sorted away in {@link merged}). Empty until the first read resolves. */
  private readonly backfill = computed<readonly LoggedEvent[]>(() => backfillEvents(this.activityQuery.data()));

  /** The backfill and live feeds merged and deduped by `key` (see the class doc),
   * oldest → newest, capped at {@link RENDER_LIMIT} — the same shape
   * {@link FleetLiveUpdates.log} produces on its own, so {@link rows} below needs no
   * branch on which source a given entry came from. */
  private readonly merged = computed<readonly LoggedEvent[]>(() =>
    mergeActivityFeeds(this.backfill(), this.live.log(), RENDER_LIMIT),
  );

  /** The merged feed newest-first, each frame shaped into its display row. */
  protected readonly rows = computed<readonly ActivityRow[]>(() => activityRows(this.merged()));

  /**
   * The panel's async state: the backfill query drives loading/error/empty/ready
   * (`asyncState`) — a first in-flight fetch renders `'loading'`, not `'empty'` —
   * with one override: a hard SSE auth failure (`authFailed`, the stream closed on a
   * `401` with no reconnect scheduled) always reads as `'error'`, since that's a real
   * degraded state the backfill query alone never observes (it only ever runs once).
   * Auth failure wins if both are somehow true.
   */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    activityPanelState(
      this.live.status() === 'closed' && this.live.authFailed(),
      this.activityQuery,
      this.rows().length === 0,
    ),
  );
}

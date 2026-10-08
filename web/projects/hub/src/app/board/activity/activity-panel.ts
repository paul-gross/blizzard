import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { type KitAsyncStateValue, FleetLiveUpdates, type LoggedEvent } from 'fleet';
import { ACTIVITY_LIMIT, injectHubActivityQuery } from './activity.query';
import { ActivityFeedView, type ActivityRow } from './activity-view';
import { activityPanelState, activityRows, backfillEvents, mergeActivityFeeds } from './activity-panel.model';

/** The rendered-row cap for the merged backfill + live feed — the backfill read's own limit. */
const RENDER_LIMIT = ACTIVITY_LIMIT;

/**
 * The Activity feed panel's **container** (`bzh:frontend-container-presentational`): the
 * one-shot backfill read and the live ring, merged into one rendered list by
 * {@link mergeActivityFeeds}.
 */
@Component({
  selector: 'app-activity-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ActivityFeedView],
  templateUrl: './activity-panel.html',
  styleUrl: './activity-panel.css',
})
export class ActivityPanel {
  private readonly live = inject(FleetLiveUpdates);
  protected readonly activityQuery = injectHubActivityQuery();

  private readonly backfill = computed<readonly LoggedEvent[]>(() => backfillEvents(this.activityQuery.data()));

  private readonly merged = computed<readonly LoggedEvent[]>(() =>
    mergeActivityFeeds(this.backfill(), this.live.log(), RENDER_LIMIT),
  );

  protected readonly rows = computed<readonly ActivityRow[]>(() => activityRows(this.merged()));

  /** The panel's async state — {@link activityPanelState}. */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    activityPanelState(
      this.live.status() === 'closed' && this.live.authFailed(),
      this.activityQuery,
      this.rows().length === 0,
    ),
  );
}

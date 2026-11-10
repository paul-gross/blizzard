import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { FleetWhen, KitBadge, type runnerApi, STATUS_TONE, toneColor } from 'fleet';

import type { hubLink } from '../../core/runner-health.model';
import type { HubSummary } from './hub-panel.model';

@Component({
  selector: 'app-hub-panel-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge],
  templateUrl: './hub-panel-view.html',
  styleUrl: './hub-panel-view.css',
})
export class HubPanelView {
  readonly summary = input.required<HubSummary | null>();
  readonly link = input.required<ReturnType<typeof hubLink>>();
  readonly fleet = input.required<runnerApi.FleetSummaryView | null>();
  readonly fleetStale = input.required<boolean>();

  protected readonly issueColor = toneColor;
  protected readonly bucketColor = {
    ready: toneColor(STATUS_TONE('ready')),
    running: toneColor(STATUS_TONE('running')),
    waiting: toneColor(STATUS_TONE('waiting_on_human')),
    needs: toneColor(STATUS_TONE('needs_human')),
  } as const;
}

import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { FleetWhen, domainStatusTone } from 'fleet/core';
import { KitBadge, toneColor } from 'fleet/kit';

import type { hubLink } from '../../core/runner-health.model';
import type { FleetSummary } from '../../core/runner-status.domain';
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
  readonly fleet = input.required<FleetSummary | null>();
  readonly fleetStale = input.required<boolean>();

  protected readonly issueColor = toneColor;
  protected readonly bucketColor = {
    ready: toneColor(domainStatusTone('ready')),
    running: toneColor(domainStatusTone('running')),
    waiting: toneColor(domainStatusTone('waiting_on_human')),
    needs: toneColor(domainStatusTone('needs_human')),
  } as const;
}

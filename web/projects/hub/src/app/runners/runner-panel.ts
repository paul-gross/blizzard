import { ChangeDetectionStrategy, Component } from '@angular/core';

import { RunnerPanelView } from './runner-view';
import { injectRunnerRows } from './runner-rows';

/**
 * The runner panel — the fleet at a glance in the board's right rail: its subscriptions' pace, one
 * compact row per registered runner, each linking to the Fleet tab where every detail and action
 * lives, and its environment pools.
 *
 * A container: it folds the registry + chunks reads via {@link injectRunnerRows} and renders the
 * presentational {@link RunnerPanelView}. Freshness: `EVENT_INVALIDATION_REGISTRY`
 * (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
@Component({
  selector: 'app-runner-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RunnerPanelView],
  templateUrl: './runner-panel.html',
})
export class RunnerPanel {
  private readonly runnerRows = injectRunnerRows();
  protected readonly rows = this.runnerRows.rows;
  protected readonly cards = this.runnerRows.cards;
  protected readonly state = this.runnerRows.state;
}

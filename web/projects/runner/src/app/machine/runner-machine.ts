import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitPanel } from 'fleet';

import type { RunnerOwner } from '../core/panel-reads.model';
import { LocalInfo } from './app-info';
import { LocalHarnessHealth } from './harness-health';
import { LocalPauseControl } from './app-pause-control';

/**
 * One runner's own machine on the machine page — its pause control, its hub link and fleet
 * strip ({@link LocalInfo}), and its harness bindings' health ({@link LocalHarnessHealth}),
 * under the runner's `host/runner` name. Each is a self-fetching container over that
 * runner's dashboard read, so this section owns nothing beyond laying them out.
 */
@Component({
  selector: 'app-runner-machine',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, LocalHarnessHealth, LocalInfo, LocalPauseControl],
  templateUrl: './runner-machine.html',
  styleUrl: './runner-machine.css',
})
export class RunnerMachine {
  readonly runner = input.required<RunnerOwner>();
}

import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitPanel } from 'fleet';

import { LocalInfo } from './app-info';
import { LocalHarnessHealth } from './harness-health';
import { LocalPauseControl } from './app-pause-control';

/**
 * The `runners/:runnerId/machine` route — the active runner's own machine: its pause
 * control, its hub link and fleet strip ({@link LocalInfo}), and its harness bindings'
 * health ({@link LocalHarnessHealth}). Each is a self-fetching container over the
 * runner's shared dashboard read, so this page owns nothing beyond laying them out.
 */
@Component({
  selector: 'app-machine-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, LocalHarnessHealth, LocalInfo, LocalPauseControl],
  templateUrl: './machine-page.html',
  styleUrl: './machine-page.css',
})
export class MachinePage {}

import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { KitPanel } from 'fleet';

import { LocalPauseControl } from './app-pause-control';
import { LocalThrottle } from './throttle';

@Component({
  selector: 'app-machine-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, RouterLink, LocalPauseControl, LocalThrottle],
  templateUrl: './machine-page.html',
})
export class MachinePage {
  protected readonly runnerId = inject(ActivatedRoute).snapshot.paramMap.get('runnerId')!;
}

import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
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
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });
  protected readonly runnerId = computed(() => this.params().get('runnerId') ?? '');
}

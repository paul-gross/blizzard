import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import { injectLensedProject } from '../../core/lensed-project';
import { injectHubRunnersQuery } from '../../core/runners.query';
import { unservedBannerText } from './unserved-banner.model';

/**
 * The board's unserved-project banner: under a project lens, a project no live runner serves says so above the board,
 * so its waiting work reads as unserved rather than slow. On All, and while a live runner serves the project, it
 * renders nothing.
 */
@Component({
  selector: 'app-unserved-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './unserved-banner.html',
  styleUrl: './unserved-banner.css',
})
export class UnservedBanner {
  private readonly project = injectLensedProject();
  private readonly runnersQuery = injectHubRunnersQuery();

  protected readonly text = computed(() => unservedBannerText(this.project(), this.runnersQuery.data()));
}

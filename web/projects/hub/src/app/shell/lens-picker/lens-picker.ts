import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { KitBackBar } from 'fleet';
import { KitSwatch, LensLink } from 'fleet/shell';

import { ProjectLens, lensedUrl } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';
import { lensChoices } from '../nav/project-lens.model';
import { LENS_PICKER_FROM, lensPickerFrom } from './lens-picker.model';

/**
 * `/lens` — the phone's project lens picker, opened from the titlebar's lens name: a back row to the view it was
 * opened from, then All projects and each of the tenant's projects, the lens's own choice selected. Choosing one puts
 * the lens on it and returns to that view under the new lens.
 */
@Component({
  selector: 'app-lens-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBackBar, KitSwatch, LensLink, RouterLink],
  templateUrl: './lens-picker.html',
  styleUrl: './lens-picker.css',
})
export class LensPicker {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly projects = injectProjectsQuery();

  /** The unlensed view the picker returns to; its back row keeps the lens the board is still on through `lensLink`. */
  protected readonly from = toSignal(
    inject(ActivatedRoute).queryParamMap.pipe(map((params) => lensPickerFrom(params.get(LENS_PICKER_FROM)))),
    { initialValue: '/board' },
  );

  protected readonly choices = computed(() => lensChoices(this.projects.data() ?? []));

  protected readonly current = this.lens.slug;

  protected choose(slug: string | null): void {
    this.lens.dismissNotice();
    void this.router.navigateByUrl(lensedUrl(this.from(), slug));
  }
}

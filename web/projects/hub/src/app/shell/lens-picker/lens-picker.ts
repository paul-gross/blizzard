import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { map } from 'rxjs';

import { asyncState } from 'fleet';

import { LENS_PICKER_FROM, ProjectLens, lensSource } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';
import { lensChoices } from '../nav/project-lens.model';
import { LensPickerView } from './lens-picker-view';

/**
 * `/lens` — the phone's project lens picker, opened from the titlebar's lens name. It reads the location it was opened
 * from, offers Back to it, and on a choice puts the lens on that project keeping only the view — the same switch the
 * desktop lens control makes. {@link ProjectLens} owns how the source is read and the switch made.
 */
@Component({
  selector: 'app-lens-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LensPickerView],
  templateUrl: './lens-picker.html',
  styleUrl: './lens-picker.css',
})
export class LensPicker {
  private readonly lens = inject(ProjectLens);
  private readonly projects = injectProjectsQuery();

  /** The unlensed location the picker was opened from. */
  private readonly source = toSignal(
    inject(ActivatedRoute).queryParamMap.pipe(map((params) => lensSource(params.get(LENS_PICKER_FROM)))),
    { initialValue: lensSource(null) },
  );

  /** Back: the source, under the lens the board is still on. */
  protected readonly backLink = computed(() => this.lens.linkTree(this.source()));

  protected readonly choices = computed(() => lensChoices(this.projects.data() ?? []));

  protected readonly state = computed(() => asyncState(this.projects, this.projects.data()?.length === 0));

  protected readonly current = this.lens.slug;

  protected choose(slug: string | null): void {
    void this.lens.switchTo(slug, this.source());
  }
}

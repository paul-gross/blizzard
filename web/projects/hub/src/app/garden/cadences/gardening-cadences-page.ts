import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { Router, RouterOutlet } from '@angular/router';
import {
  asyncState,
  FlagStore,
  KitButton,
  KitMasterDetail,
  KitPanel,
  type KitAsyncStateValue,
  submitForm,
  ViewportService,
} from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { firstRowToOpen } from '../../core/first-row';
import { injectLensedProject } from '../../core/lensed-project';
import { ProjectLens } from '../../core/project-lens';
import { injectChildRouteParam } from '../../core/route-state';
import { injectHubCadencesQuery } from '../core/cadences.query';
import { type Cadence, type CadenceDraft, EMPTY_CADENCE_DRAFT } from '../core/cadence.domain';
import { cadenceCreateRules } from './cadence.form';
import { injectCadenceService } from './cadence.service';
import { FleetCadenceForm } from './cadence-form';
import { FleetCadenceList, type CadenceRowVm } from './cadence-list';
import { cadenceRows, flagOffLine, presentCadenceSlug } from './gardening-cadences-page.model';

/**
 * The `/gardening/cadences` sub-tab — the project's cadences, beside a `<router-outlet>` holding whichever cadence the
 * URL names (`gardening-cadence-detail.ts`), and a form creating one that opens the new cadence's detail.
 *
 * While the `cadences` flag is off the list says the cadences mint nothing; the tab and its records stay, since the
 * hub serves and edits them either way.
 *
 * A container: it injects the cadence query and the cadence service, owns the create draft and its form, and forwards
 * plain view models and the field tree to the presentational {@link FleetCadenceList} and {@link FleetCadenceForm}.
 */
@Component({
  selector: 'app-gardening-cadences-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetCadenceForm, FleetCadenceList, KitButton, KitMasterDetail, KitPanel, RouterOutlet],
  templateUrl: './gardening-cadences-page.html',
  styleUrl: './gardening-cadences-page.css',
})
export class GardeningCadencesPage {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly flags = inject(FlagStore);

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  private readonly cadencesQuery = injectHubCadencesQuery();
  private readonly meQuery = injectMeQuery();
  private readonly cadenceService = injectCadenceService();
  private readonly project = injectLensedProject();

  protected readonly projectName = computed(() => this.project()?.name ?? this.lens.slug() ?? '');
  private readonly cadences = computed<readonly Cadence[]>(() => this.cadencesQuery.data() ?? []);

  /** The line saying cadences mint nothing — `null` while the `cadences` flag is on. */
  protected readonly flagOffLine = computed(() => flagOffLine(this.flags.on('cadences')));

  /** Whether the current identity may author cadences (`graph:edit`). */
  protected readonly canEdit = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  protected readonly cadenceSlugParam = injectChildRouteParam('cadenceSlug');

  protected readonly selectedCadenceSlug = computed<string | null>(() =>
    presentCadenceSlug(this.cadenceSlugParam(), this.cadences()),
  );

  protected readonly cadenceRows = computed<readonly CadenceRowVm[]>(() => cadenceRows(this.cadences()));

  protected readonly cadencesState = computed<KitAsyncStateValue>(() =>
    asyncState(this.cadencesQuery, this.cadenceRows().length === 0),
  );

  /** Whether the create form is open. */
  protected readonly creating = signal(false);
  private readonly createDraft = signal<CadenceDraft>(EMPTY_CADENCE_DRAFT);
  protected readonly createForm = form(this.createDraft, cadenceCreateRules);

  protected selectCadence(slug: string): void {
    void this.router.navigate(this.lens.link(['/gardening', 'cadences', slug]), { queryParamsHandling: 'preserve' });
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/gardening', 'cadences']), { queryParamsHandling: 'preserve' });
  }

  protected openCreate(): void {
    this.createForm().reset({ ...EMPTY_CADENCE_DRAFT });
    this.creating.set(true);
  }

  protected closeCreate(): void {
    this.creating.set(false);
  }

  /** Saves the create draft; once it settles, closes the form and opens the created cadence. */
  protected async onCreate(): Promise<void> {
    const slug = this.createDraft().slug.trim();
    if (await submitForm(this.createForm, (draft) => this.cadenceService.create(draft))) {
      this.creating.set(false);
      this.selectCadence(slug);
    }
  }

  constructor() {
    effect(() => {
      const settled = !this.cadencesQuery.isPending() && !this.cadencesQuery.isError();
      const slugs = this.cadenceRows().map((row) => row.slug);
      const first = firstRowToOpen(this.cadenceSlugParam(), slugs, settled, this.mobile());
      if (first === null) return;
      void this.router.navigate(this.lens.link(['/gardening', 'cadences', first]), {
        replaceUrl: true,
        queryParamsHandling: 'preserve',
      });
    });
  }
}

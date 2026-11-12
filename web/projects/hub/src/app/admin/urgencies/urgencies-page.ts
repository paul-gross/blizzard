import { type CdkDragDrop } from '@angular/cdk/drag-drop';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, errorMessage, injectPendingMutationVariables, KitMasterDetail, ViewportService, type UrgencyPositionRequest, type UrgencyView } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectUrgenciesQuery } from '../../core/urgencies.query';
import { ProjectLens } from '../../core/project-lens';
import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import { type ConfigFormValues } from '../config-edit.model';
import { LIFECYCLE_FILTER_PARAM, lifecycleFilterParam, parseLifecycleFilter } from '../config-filter.model';
import { liveUrgencies, overlayUrgencies, urgencyCreate, urgencyOptions, urgencyPosition, visibleUrgencies } from './urgencies.model';
import { injectCreateUrgencyMutation, injectDefaultUrgencyMutation, injectPositionUrgencyMutation, urgencyDefaultMutationKey, urgencyPositionMutationKey } from './urgencies.mutations';
import { injectSettingsQuery } from './urgencies.query';
import { UrgenciesList } from './urgencies-list';

@Component({
  selector: 'app-urgencies-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMasterDetail, RouterOutlet, UrgenciesList],
  templateUrl: './urgencies-page.html', styleUrls: ['../config-page-host.css', './urgencies-page.css'],
})
export class UrgenciesPage {
  private readonly query = injectUrgenciesQuery();
  protected readonly settings = injectSettingsQuery();
  private readonly me = injectMeQuery();
  private readonly filters = injectQueryFilters();
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);
  protected readonly createMutation = injectCreateUrgencyMutation();
  private readonly positionMutation = injectPositionUrgencyMutation();
  private readonly defaultMutation = injectDefaultUrgencyMutation();
  private readonly pendingMoves = injectPendingMutationVariables<UrgencyPositionRequest>(urgencyPositionMutationKey);
  private readonly pendingDefaults = injectPendingMutationVariables<{ key: string; revision: number }>(urgencyDefaultMutationKey);
  protected readonly createOpen = signal(false);
  protected readonly createError = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly selected = injectChildRouteParam('key');
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly canEdit = computed(() => !this.mobile() && hasPermission(this.me.data(), 'config:edit'));
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  private readonly overlaid = computed(() => overlayUrgencies(this.query.data() ?? [], this.pendingMoves(), this.pendingDefaults()));
  protected readonly rows = computed(() => visibleUrgencies(this.overlaid(), this.lifecycle()));
  protected readonly live = computed(() => liveUrgencies(this.overlaid()));
  protected readonly defaultOptions = computed(() => urgencyOptions(this.live()));
  protected readonly defaultKey = computed(() => this.pendingDefaults().at(-1)?.key ?? this.settings.data()?.default_urgency.urgency_id ?? null);
  protected readonly state = computed(() => asyncState(this.query, this.rows().length === 0));
  protected readonly pending = computed(() => this.defaultMutation.isPending() || this.positionMutation.isPending());
  protected onFilter(value: string): void { this.filters.patch({ [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value) }); }
  protected onPick(key: string): void { void this.router.navigate(this.lens.link(['/admin', 'urgencies', key]), { queryParamsHandling: 'preserve' }); }
  protected onBack(): void { void this.router.navigate(this.lens.link(['/admin', 'urgencies']), { queryParamsHandling: 'preserve' }); }
  protected onCreate(values: ConfigFormValues): void {
    if (!this.canEdit()) return;
    const body = urgencyCreate(values);
    this.createError.set(null);
    this.createMutation.mutate(body, { onSuccess: (record) => { this.createOpen.set(false); this.onPick(record.urgency_id); }, onError: (e) => this.createError.set(errorMessage(e, 'Create failed.')) });
  }
  protected onDefault(key: string): void {
    const settings = this.settings.data();
    if (!this.canEdit() || !settings || !key || key === settings.default_urgency.urgency_id) return;
    this.actionError.set(null);
    this.defaultMutation.mutate({ key, revision: settings.revision }, { onError: (e) => this.actionError.set(errorMessage(e, 'Default change failed.')) });
  }
  protected onDrop(event: CdkDragDrop<readonly UrgencyView[]>): void {
    if (!this.canEdit() || this.lifecycle() !== 'active' || this.pending()) return;
    const body = urgencyPosition(this.rows(), event.previousIndex, event.currentIndex);
    if (!body) return;
    this.actionError.set(null);
    this.positionMutation.mutate(body, { onError: (e) => this.actionError.set(errorMessage(e, 'Reorder failed.')) });
  }
}

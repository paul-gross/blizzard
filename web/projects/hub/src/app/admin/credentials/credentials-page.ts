import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { form } from '@angular/forms/signals';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, type KitAsyncStateValue, submitForm, ViewportService } from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import { CredentialForm } from './credential-form';
import { EMPTY_CREDENTIAL_DRAFT, type CredentialDraft, settingDrafts, withForge } from './credential.domain';
import { credentialRules } from './credential.form';
import { injectCredentialService } from './credential.service';
import { includeRetired, LIFECYCLE_FILTER_PARAM, lifecycleEmptyText, lifecycleFilterParam, parseLifecycleFilter } from '../config-filter.model';
import { ConfigMaster } from '../config-master';
import { injectForgeChoicesQuery } from '../forges/forges.query';
import { credentialRows, credentialCreateFields, createSettingsUnavailable, removedCreateSettings } from './credentials.model';
import { injectCredentialsQuery } from './credentials.query';

/**
 * `/admin/credentials` — the credential list beside a `<router-outlet>` holding the record the URL
 * names (`credential-detail.ts`). The filter rides the query string and the selection the child
 * route. A container over the presentational {@link ConfigMaster}.
 */
@Component({
  selector: 'app-credentials-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CredentialForm, ConfigMaster, RouterOutlet],
  templateUrl: './credentials-page.html', styleUrl: '../config-page-host.css',
})
export class CredentialsPage {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();
  private readonly meQuery = injectMeQuery();
  private readonly credentialsQuery = injectCredentialsQuery(() => includeRetired(this.lifecycle()));
  private readonly forgesQuery = injectForgeChoicesQuery(() => false);
  private readonly service = injectCredentialService();
  private readonly bindings = computed(() => this.credentialsQuery.data()?.bindings ?? []);
  private readonly forgeBindings = computed(() => this.credentialsQuery.data()?.forgeBindings ?? []);
  protected readonly fields = computed(() => credentialCreateFields(this.forgesQuery.data() ?? [], this.forgeBindings()));
  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  protected readonly rows = computed(() => credentialRows(this.credentialsQuery.data()?.credentials ?? [], this.lifecycle()));
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.credentialsQuery, this.rows().length === 0));
  protected readonly emptyText = computed(() => lifecycleEmptyText('credentials', this.lifecycle()));
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));
  protected readonly createOpen = signal(false);
  private readonly draft = signal<CredentialDraft>({ ...EMPTY_CREDENTIAL_DRAFT });
  protected readonly createForm = form(this.draft, credentialRules);
  protected readonly settingsUnavailable = computed(() => createSettingsUnavailable(this.draft(), this.bindings()));
  protected readonly removedSettings = computed(() => removedCreateSettings(this.draft(), this.bindings()));

  private readonly syncSettings = effect(() => {
    const bindings = this.bindings();
    if (!this.createOpen()) return;
    untracked(() => this.draft.update((draft) => {
      const definitions = settingDrafts(bindings);
      const settings = draft.settings.map((setting) => {
        const current = definitions.find((item) => item.binding === setting.binding && item.key === setting.key);
        return current ? { ...setting, type: current.type } : setting;
      });
      for (const definition of definitions) {
        if (!settings.some((setting) => setting.binding === definition.binding && setting.key === definition.key)) settings.push(definition);
      }
      return { ...draft, settings };
    }));
  });

  protected onFilter(value: string): void {
    this.filters.patch({ [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value) });
  }
  protected onPick(key: string): void {
    void this.router.navigate(this.lens.link(['/admin', 'credentials', key]), { queryParamsHandling: 'preserve' });
  }
  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'credentials']), { queryParamsHandling: 'preserve' });
  }
  protected onNew(): void {
    this.createForm().reset({ ...EMPTY_CREDENTIAL_DRAFT, settings: settingDrafts(this.bindings()) });
    this.createOpen.set(true);
  }
  protected onFieldChanged(key: string): void {
    if (key !== 'forge') return;
    this.draft.update((draft) => withForge(draft, draft.forge, this.forgeBindings()));
  }
  protected async onCreate(): Promise<void> {
    if (this.settingsUnavailable() || this.removedSettings().length) return;
    const slug = this.draft().slug.trim();
    if (!(await submitForm(this.createForm, (draft) => this.service.create(draft, this.bindings())))) return;
    this.createOpen.set(false);
    void this.router.navigate(this.lens.link(['/admin', 'credentials', slug]), { queryParamsHandling: 'preserve' });
  }
}

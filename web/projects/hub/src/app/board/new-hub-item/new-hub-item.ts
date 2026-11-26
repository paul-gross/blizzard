import { ChangeDetectionStrategy, Component, computed, inject, output, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { createRefusalState, submitForm } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectProjectDomainsQuery } from '../../core/projects.query';
import { injectDomainUrgenciesQuery } from '../../core/urgencies.query';
import { EMPTY_NEW_HUB_ITEM_DRAFT, type NewHubItemDraft, startingDraft } from './new-hub-item.domain';
import { newHubItemRules } from './new-hub-item.form';
import { projectOptions, urgencyOptions } from './new-hub-item.model';
import { injectNewHubItemService } from './new-hub-item.service';
import { NewHubItemView } from './new-hub-item-view';

@Component({
  selector: 'app-new-hub-item',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NewHubItemView],
  templateUrl: './new-hub-item-container.html',
})
export class NewHubItem {
  readonly created = output<string>();
  private readonly me = injectMeQuery();
  protected readonly lens = inject(ProjectLens);
  private readonly projects = injectProjectDomainsQuery();
  private readonly urgencies = injectDomainUrgenciesQuery();
  private readonly service = injectNewHubItemService();
  protected readonly allowed = computed(() => hasPermission(this.me.data(), 'chunk:control'));
  protected readonly projectOptions = computed(() => projectOptions(this.projects.data() ?? []));
  protected readonly urgencyOptions = computed(() => urgencyOptions(this.urgencies.data() ?? []));
  protected readonly open = signal(false);
  private readonly refusal = createRefusalState<NewHubItemDraft>();
  protected readonly form = form(signal<NewHubItemDraft>(EMPTY_NEW_HUB_ITEM_DRAFT), (p) => {
    apply(p, newHubItemRules);
    apply(p, this.refusal.schema);
  });

  protected start(): void {
    if (this.form().submitting()) return;
    const urgency = this.urgencies.data()?.find((u) => u.default && !u.retired)?.id ?? null;
    this.refusal.clear();
    this.form().reset(startingDraft(this.lens.slug(), urgency));
    this.open.set(true);
  }

  protected close(): void {
    if (!this.form().submitting()) this.open.set(false);
  }

  protected async submit(): Promise<void> {
    if (!this.allowed()) return;
    await submitForm(
      this.form,
      (draft) =>
        this.service.create(draft, (chunkId) => {
          this.open.set(false);
          this.created.emit(chunkId);
        }),
      { refusalState: this.refusal },
    );
  }
}

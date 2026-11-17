import { ChangeDetectionStrategy, Component, computed, inject, output, signal } from '@angular/core';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';
import { injectUrgenciesQuery } from '../../core/urgencies.query';
import { createErrorText, projectOptions, urgencyOptions } from './new-hub-item.model';
import { injectCreateHubItemMutation } from './new-hub-item.mutations';
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
  private readonly projects = injectProjectsQuery();
  private readonly urgencies = injectUrgenciesQuery();
  private readonly mutation = injectCreateHubItemMutation();
  protected readonly allowed = computed(() => hasPermission(this.me.data(), 'chunk:control'));
  protected readonly projectOptions = computed(() => projectOptions(this.projects.data() ?? []));
  protected readonly urgencyOptions = computed(() => urgencyOptions(this.urgencies.data() ?? []));
  protected readonly open = signal(false);
  protected readonly title = signal('');
  protected readonly body = signal('');
  protected readonly project = signal<string | null>(null);
  protected readonly urgency = signal<string | null>(null);
  /**
   * Latched for the synchronous gap between `submit()` and the mutation's own `isPending()`, which the query adapter
   * publishes on a later tick — without it a reopen or dismissal in that gap would slip past the guard.
   */
  private readonly submitting = signal(false);
  protected readonly pending = computed(() => this.submitting() || this.mutation.isPending());
  protected readonly error = computed(() => createErrorText(this.mutation.error()));

  protected start(): void {
    if (this.pending()) return;
    this.title.set('');
    this.body.set('');
    this.project.set(this.lens.slug());
    this.urgency.set(this.urgencies.data()?.find((u) => u.default && !u.retired)?.urgency_id ?? null);
    this.mutation.reset();
    this.open.set(true);
  }

  protected close(): void {
    if (!this.pending()) this.open.set(false);
  }

  protected async submit(): Promise<void> {
    const project = this.project();
    const urgency = this.urgency();
    if (!this.allowed() || !project || !urgency || !this.title().trim() || !this.body().trim() || this.pending()) return;
    this.submitting.set(true);
    const chunkId = await this.mutation
      .mutateAsync({ project, title: this.title().trim(), body: this.body(), urgency })
      .catch(() => null);
    this.submitting.set(false);
    if (chunkId === null) return;
    this.open.set(false);
    this.created.emit(chunkId);
  }
}

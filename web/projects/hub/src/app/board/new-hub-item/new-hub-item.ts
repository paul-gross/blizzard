import { ChangeDetectionStrategy, Component, computed, inject, output, signal } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';
import { KitButton, KitDialog, KitErrorLine, KitSelect, KitTextInput, errorMessage, hubBacklogKey, hubChunksKey } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';
import { injectUrgenciesQuery } from '../../core/urgencies.query';
import { projectOptions, urgencyOptions } from './new-hub-item.model';
import { createHubItem } from './new-hub-item.mutations';

@Component({
  selector: 'app-new-hub-item',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitErrorLine, KitSelect, KitTextInput],
  templateUrl: './new-hub-item.html',
  styleUrl: './new-hub-item.css',
})
export class NewHubItem {
  readonly created = output<string>();
  private readonly me = injectMeQuery();
  protected readonly lens = inject(ProjectLens);
  private readonly projects = injectProjectsQuery();
  private readonly urgencies = injectUrgenciesQuery();
  private readonly client = inject(QueryClient);
  protected readonly allowed = computed(() => hasPermission(this.me.data(), 'chunk:control'));
  protected readonly projectOptions = computed(() => projectOptions(this.projects.data() ?? []));
  protected readonly urgencyOptions = computed(() => urgencyOptions(this.urgencies.data() ?? []));
  protected readonly open = signal(false);
  protected readonly title = signal('');
  protected readonly body = signal('');
  protected readonly project = signal<string | null>(null);
  protected readonly urgency = signal<string | null>(null);
  protected readonly pending = signal(false);
  protected readonly error = signal<string | null>(null);

  protected start(): void {
    this.title.set('');
    this.body.set('');
    this.project.set(this.lens.slug());
    this.urgency.set(this.urgencies.data()?.find((u) => u.default && !u.retired)?.urgency_id ?? null);
    this.error.set(null);
    this.open.set(true);
  }

  protected async submit(): Promise<void> {
    const project = this.project();
    const urgency = this.urgency();
    if (!this.allowed() || !project || !urgency || !this.title().trim() || !this.body().trim() || this.pending()) return;
    this.pending.set(true);
    this.error.set(null);
    try {
      const chunkId = await createHubItem(project, this.title().trim(), this.body(), urgency);
      await Promise.all([this.client.invalidateQueries({ queryKey: hubChunksKey }), this.client.invalidateQueries({ queryKey: hubBacklogKey })]);
      this.open.set(false);
      this.created.emit(chunkId);
    } catch (error) {
      this.error.set(errorMessage(error, 'Could not create hub item.'));
    } finally {
      this.pending.set(false);
    }
  }
}

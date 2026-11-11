import { ChangeDetectionStrategy, Component, computed, input, linkedSignal, output } from '@angular/core';
import { KitButton, KitDialog, KitErrorLine, KitTextInput } from 'fleet';

import type { LinkBranchVm } from './projects.model';

/**
 * Edit one repository link's own branch: name the branch this project's chunks start from and land into, or clear it
 * so the link inherits the repository's `base_branch` again. Whether the branch exists is not checked here; a name
 * that is no git branch name is the hub's refusal, shown under the field. Presentational.
 */
@Component({
  selector: 'app-project-branch-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitErrorLine, KitTextInput],
  templateUrl: './project-branch-dialog.html',
  styleUrl: './project-branch-dialog.css',
})
export class ProjectBranchDialog {
  readonly heading = input.required<string>();
  readonly branch = input.required<LinkBranchVm>();
  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);
  readonly testidPrefix = input.required<string>();

  readonly closed = output<void>();
  /** The link's new branch, or `null` to inherit the repository's again. */
  readonly submitted = output<string | null>();

  /** The typed name, starting from the link's own branch. */
  protected readonly value = linkedSignal(() => this.branch().override ?? '');
  protected readonly canSave = computed(
    () => !this.submitting() && this.value() !== '' && this.value() !== this.branch().override,
  );
  protected readonly canInherit = computed(() => !this.submitting() && this.branch().override !== null);

  protected onSave(): void {
    if (this.canSave()) this.submitted.emit(this.value());
  }

  protected onInherit(): void {
    if (this.canInherit()) this.submitted.emit(null);
  }

  protected onClosed(): void {
    if (this.submitting()) return;
    this.closed.emit();
  }
}

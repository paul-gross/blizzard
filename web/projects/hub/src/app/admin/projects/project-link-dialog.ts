import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitButton, KitDialog, KitSelect } from 'fleet';

import type { LinkChoiceVm } from './projects.model';

/**
 * Link one record to a project: pick a work source or a repository the tenant holds and the
 * project does not link yet. A link carries nothing but the fact of the link, so the dialog
 * asks for nothing else. With nothing left to link it says where new records are added.
 */
@Component({
  selector: 'app-project-link-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitSelect],
  templateUrl: './project-link-dialog.html',
  styleUrl: './project-link-dialog.css',
})
export class ProjectLinkDialog {
  readonly heading = input.required<string>();
  /** What is picked, as a field label — "Work source", "Repository". */
  readonly fieldLabel = input.required<string>();
  readonly choices = input.required<readonly LinkChoiceVm[]>();
  /** What linking does, under the field. */
  readonly note = input.required<string>();
  /** What to say when every record is linked already. */
  readonly noneText = input.required<string>();
  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);
  readonly testidPrefix = input.required<string>();

  readonly closed = output<void>();
  readonly submitted = output<string>();

  private readonly picked = signal<string | null>(null);
  /** The pick, or the first choice before one is made. */
  protected readonly selected = computed(() => this.picked() ?? this.choices()[0]?.value ?? null);
  protected readonly canSubmit = computed(() => !this.submitting() && this.selected() !== null);

  protected onChoose(value: string): void {
    this.picked.set(value);
  }

  protected onSubmit(): void {
    const value = this.selected();
    if (this.canSubmit() && value !== null) this.submitted.emit(value);
  }

  protected onClosed(): void {
    if (this.submitting()) return;
    this.closed.emit();
  }
}

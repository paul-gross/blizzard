// exemplar: bzh:frontend-container-presentational, bzh:frontend-kit-floor — presentational: it renders the field tree its container owns.

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import {
  KitDialog,
  KitField,
  KitForm,
  KitOption,
  KitOptionGroup,
  KitSelect,
  KitTextInput,
  type KitChipOption,
} from 'fleet';

import type { ProposalAcceptanceDraft } from './proposal-acceptance.domain';

/** The Accept dialog's view — a `KitForm` over the container's `form()`: the mint or decline choice, the mint's urgency
 * and prefilled work-item body, and one reason field in both modes. The schema hides the mint fields under a decline. */
@Component({
  selector: 'app-proposal-accept-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitDialog, KitField, KitForm, KitOption, KitOptionGroup, KitSelect, KitTextInput],
  templateUrl: './proposal-accept-dialog-view.html',
  styleUrl: './proposal-accept-dialog-view.css',
})
export class ProposalAcceptDialogView {
  readonly proposalTitle = input.required<string>();
  readonly form = input.required<FieldTree<ProposalAcceptanceDraft>>();
  readonly urgencyOptions = input<readonly KitChipOption[]>([]);

  readonly closed = output<void>();
  readonly save = output<void>();

  /** Escape, a backdrop click, and Cancel all route through one close, held while an accept is in flight. */
  protected onClosed(): void {
    if (this.form()().submitting()) return;
    this.closed.emit();
  }
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitButton, KitDialog, KitField, KitForm, KitSelect, KitTextInput } from 'fleet';

import type { NewHubItemDraft } from './new-hub-item.domain';

/** The new-item entry as a dialog. Presentational: it renders the field tree it is handed; its container owns the draft, the rules, and the save. */
@Component({
  selector: 'app-new-hub-item-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitButton, KitDialog, KitField, KitForm, KitSelect, KitTextInput],
  templateUrl: './new-hub-item.html',
  styleUrl: './new-hub-item.css',
})
export class NewHubItemView {
  readonly allowed = input.required<boolean>();
  readonly lensSlug = input.required<string | null>();
  readonly projectOptions = input.required<{ value: string; label: string }[]>();
  readonly urgencyOptions = input.required<{ value: string; label: string }[]>();
  readonly open = input.required<boolean>();
  readonly form = input.required<FieldTree<NewHubItemDraft>>();
  readonly startRequested = output<void>();
  readonly closeRequested = output<void>();
  readonly saveRequested = output<void>();
}

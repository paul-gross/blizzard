import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitField, KitNumberInput, KitSelect, KitTextInput } from 'fleet';

import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import { type ConfigFieldTreeMode, type ConfigFieldView } from '../config-field-tree.model';
import { type CredentialChange, type CredentialDraft, type CredentialSettingKind } from './credential.domain';

/** The credential form's presentation. The container owns the form, choices, session and submission. */
@Component({
  selector: 'app-credential-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, FormField, KitField, KitNumberInput, KitSelect, KitTextInput],
  templateUrl: './credential-form.html',
})
export class CredentialForm {
  readonly heading = input.required<string>();
  readonly mode = input.required<ConfigFieldTreeMode>();
  readonly form = input.required<FieldTree<CredentialDraft>>();
  readonly fields = input.required<readonly ConfigFieldView<CredentialDraft>[]>();
  readonly baseline = input<CredentialDraft | null>(null);
  readonly presentationDiff = input<readonly CredentialChange[] | null>(null);
  readonly submitLabel = input.required<string>();
  readonly testidPrefix = input.required<string>();
  readonly settingsUnavailable = input(false);
  readonly removedSettings = input<readonly string[]>([]);
  readonly fieldChanged = output<string>();
  readonly save = output<void>();
  readonly closed = output<void>();

  protected choices(type: CredentialSettingKind): readonly { value: string; label: string }[] {
    return type.kind === 'choice' ? [{ value: '', label: 'Not set' }, ...type.choices.map((value) => ({ value, label: value }))] : [];
  }
}

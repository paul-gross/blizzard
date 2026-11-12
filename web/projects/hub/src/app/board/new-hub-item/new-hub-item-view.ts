import { ChangeDetectionStrategy, Component, input, model, output } from '@angular/core';
import { KitButton, KitDialog, KitErrorLine, KitSelect, KitTextInput } from 'fleet';

@Component({
  selector: 'app-new-hub-item-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog, KitErrorLine, KitSelect, KitTextInput],
  templateUrl: './new-hub-item.html',
  styleUrl: './new-hub-item.css',
})
export class NewHubItemView {
  readonly allowed = input.required<boolean>();
  readonly lensSlug = input.required<string | null>();
  readonly projectOptions = input.required<{ value: string; label: string }[]>();
  readonly urgencyOptions = input.required<{ value: string; label: string }[]>();
  readonly open = input.required<boolean>();
  readonly pending = input.required<boolean>();
  readonly error = input.required<string | null>();
  readonly title = model.required<string>();
  readonly body = model.required<string>();
  readonly project = model.required<string | null>();
  readonly urgency = model.required<string | null>();
  readonly startRequested = output<void>();
  readonly closeRequested = output<void>();
  readonly submitRequested = output<void>();
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitButton } from './kit-button';
import { KitDialog } from './kit-dialog';

/** A presentational confirmation prompt built from the shared dialog and action chrome. */
@Component({
  selector: 'fleet-kit-confirm-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitDialog],
  templateUrl: './kit-confirm-dialog.html',
  styleUrl: './kit-confirm-dialog.css',
})
export class KitConfirmDialog {
  readonly open = input.required<boolean>();
  readonly heading = input.required<string>();
  readonly message = input.required<string>();
  readonly confirmLabel = input.required<string>();
  readonly variant = input.required<'primary' | 'danger'>();
  readonly testid = input.required<string>();

  readonly confirmed = output<void>();
  readonly cancelled = output<void>();

  protected onConfirmed(): void {
    this.confirmed.emit();
  }

  protected onCancelled(): void {
    this.cancelled.emit();
  }
}

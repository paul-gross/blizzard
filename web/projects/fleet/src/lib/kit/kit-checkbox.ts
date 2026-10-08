import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/** A native independent choice with shared checkbox chrome and controlled state. */
@Component({
  selector: 'fleet-kit-checkbox',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-checkbox.html',
  styleUrl: './kit-checkbox.css',
})
export class KitCheckbox {
  readonly checked = input(false);
  readonly disabled = input(false);
  readonly label = input<string | null>(null);
  readonly ariaLabel = input<string | null>(null);
  readonly testid = input<string | null>(null);
  readonly checkedChange = output<boolean>();

  protected changed(event: Event): void {
    this.checkedChange.emit((event.target as HTMLInputElement).checked);
  }
}

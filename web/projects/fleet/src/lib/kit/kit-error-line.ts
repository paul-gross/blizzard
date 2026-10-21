import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The inline line a failed user gesture reports itself on — one red `--fs-sm` line under the control that failed,
 * wrapping an unbroken message instead of overflowing. Presentational: the caller feeds it the failure's message and the
 * line renders nothing while there is none. It is inserted only once a message exists, so `role="alert"` announces the
 * failure when it appears. It carries no margin; the host layout owns the spacing around it.
 */
@Component({
  selector: 'fleet-kit-error-line',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-error-line.html',
  styleUrl: './kit-error-line.css',
})
export class KitErrorLine {
  /** The failure to show; `null` renders nothing. */
  readonly message = input<string | null>(null);

  /** The line's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);
}

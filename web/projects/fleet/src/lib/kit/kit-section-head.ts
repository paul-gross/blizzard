import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * A section heading — the small engraved uppercase tag that opens a section, on a
 * baseline row that can carry trailing content beside it (projected by default).
 * Deliberately not an `h*`: it labels a region without adding to the document
 * outline, and `labelId` lands on the tag so a section's `aria-labelledby` can
 * point at it. A consumer insets it horizontally through `--kit-section-head-pad`.
 */
@Component({
  selector: 'fleet-kit-section-head',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-section-head.html',
  styleUrl: './kit-section-head.css',
})
export class KitSectionHead {
  readonly label = input.required<string>();
  /** The tag's `id`, for a section's `aria-labelledby`. */
  readonly labelId = input<string | null>(null);
  /** Rendered after the label as `label · count`. */
  readonly count = input<number | null>(null);
}

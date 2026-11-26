import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * A section heading — the small engraved uppercase tag that opens a section, on a
 * baseline row that can carry trailing content beside it (projected by default).
 * A section sits outside the document outline by default; `level` opts it in,
 * rendering the tag as that heading element. `labelId` lands on the tag either
 * way, so a section's `aria-labelledby` can point at it. A consumer insets it
 * horizontally through `--kit-section-head-pad`.
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
  /** The heading level of the tag; `null` renders it as a non-heading. */
  readonly level = input<2 | 3 | 4 | null>(null);

  protected readonly text = computed(() => (this.count() === null ? this.label() : `${this.label()} · ${this.count()}`));
}

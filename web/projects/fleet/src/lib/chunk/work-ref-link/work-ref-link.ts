import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { WorkRefView } from '../../api/hub';

/** How a {@link WorkRefLink} looks: `inline` and `wrapping` are the identity line's two header looks — one
 * clipped with an ellipsis, one breaking mid-token for a phone-width page — and `chip` is the row/card chip. */
export type WorkRefLinkAppearance = 'inline' | 'wrapping' | 'chip';

/**
 * One work ref as a link out to its work source — the single place a chunk's work item is written as a link,
 * for the dock headers, the routed page header, and the runner's row and card views. Presentational, no
 * injection: the ref comes in as the wire's `WorkRefView`, and any `WorkItemEntry` satisfies it structurally.
 *
 * With a `web_url` it is an anchor opening in a new tab; without one (an unconfigured source) it degrades to
 * plain text, no broken link. The text is the ref's `label`, else `source#ref`. A click on the anchor stops
 * propagating, so a link inside a clickable row or card opens the link rather than selecting the row. The
 * host is `display: contents`, so the caller's own `@for` and separator layout decides where the link sits.
 */
@Component({
  selector: 'fleet-work-ref-link',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './work-ref-link.html',
  styleUrl: './work-ref-link.css',
})
export class WorkRefLink {
  /** The work ref to render. */
  readonly workRef = input.required<WorkRefView>();

  /** Which look to wear. */
  readonly appearance = input<WorkRefLinkAppearance>('inline');

  /** The `data-testid` carried by the anchor or span, for the caller's specs. */
  readonly testid = input<string | null>(null);
}

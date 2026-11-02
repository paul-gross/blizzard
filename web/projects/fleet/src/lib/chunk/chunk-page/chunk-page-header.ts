import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ChunkDetail, RouteView, WorkRefView } from '../../api/hub';
import { ChunkActionsMenu } from '../chunk-detail/chunk-actions-menu';
import type { ChunkControl } from '../chunk-detail/chunk-control';
import { injectRunnerHostNames, runnerDisplayName, runnerTitle } from '../../core/runner-display-name';
import { KitSwatch } from '../../kit/kit-swatch';
import { toneColor } from '../../kit/kit-badge';
import type { Tone } from '../../kit/tone';
import { WorkRefLink } from '../work-ref-link/work-ref-link';

/**
 * The chunk-detail-page identity header — the chunk's own id beside its
 * derived-status badge. Presentational, no injection: an input for the id,
 * the status text, and the {@link Tone} it colors the badge by.
 *
 * Renders the **full** `ch_…` id, not `compactRef`'s short form
 * (`compact-ref.ts`). That id is a single unbroken token, so `.cid` allows a
 * mid-token break (`overflow-wrap: anywhere`, with `min-width: 0` so the flex
 * item can shrink) rather than widening a phone-width page.
 */
@Component({
  selector: 'fleet-chunk-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [WorkRefLink, KitSwatch, ChunkActionsMenu],
  templateUrl: './chunk-page-header.html',
  styleUrl: './chunk-page-header.css',
})
export class ChunkPageHeader {
  /** The chunk's full `ch_…` id — never `compactRef`'d here. */
  readonly chunkId = input.required<string>();

  /** The chunk's current status, rendered verbatim on the badge. */
  readonly status = input.required<string>();

  /** The derived {@link Tone} the status text colors by — resolved by the caller
   * before this component ever sees the status (`bzh:frontend-formatters`). */
  readonly tone = input.required<Tone>();

  /** The work items this chunk serves, rendered first on the line. */
  readonly pointers = input<readonly WorkRefView[]>([]);

  /** The chunk's project name, leading the line; `null` omits the project fact. */
  readonly projectName = input<string | null>(null);

  /** The project's swatch color; `null` draws the name alone. */
  readonly projectColor = input<string | null>(null);

  /** The chunk's live route, or `null` while no runner holds it. */
  readonly route = input<RouteView | null>(null);

  /** Each runner's host name by runner id — empty where the app provides no registry,
   * so the runner keeps the compact-id form. */
  private readonly runnerHosts = injectRunnerHostNames();

  /** The runner holding the route as `host/runner`, or `null` while none does. */
  protected readonly claimant = computed<string | null>(() => {
    const route = this.route();
    return route ? runnerDisplayName(route.runner_id, route.runner_name, this.runnerHosts().get(route.runner_id)) : null;
  });

  protected readonly claimantTitle = computed<string | null>(() => {
    const route = this.route();
    return route ? runnerTitle(route.runner_id, route.runner_name, this.runnerHosts().get(route.runner_id)) : null;
  });

  /** The status text's own color, resolved off {@link tone} (`kit-badge.ts` owns the
   * mapping). */
  protected readonly statusColor = computed<string>(() => toneColor(this.tone()));

  /** The chunk aggregate the actions menu gates on; `null` hides the menu. */
  readonly detail = input<ChunkDetail | null>(null);

  /** The daemon's chunk-control port behind the ⋮ menu; `null` hides the menu. */
  readonly control = input<ChunkControl | null>(null);

  /** Run once the hub confirms a delete from the ⋮ menu. */
  readonly afterDelete = input<() => void>(() => undefined);
}

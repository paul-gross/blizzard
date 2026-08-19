import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { GraphDetail, GraphExplorer } from 'fleet';
import { map } from 'rxjs';

/**
 * The `/graphs` route — the graph explorer (paul-gross/blizzard#70 phase 3): a
 * master/detail layout with {@link GraphExplorer} (the name-grouped lineage list)
 * beside {@link GraphDetail} (the selected version's structure). Both `/graphs` and
 * `/graphs/:graphId` render this one component (see `app.routes.ts`) so the list
 * never disappears on selection; the optional `graphId` route param drives which
 * version the detail shows, making the selection refresh-safe and deep-linkable.
 */
@Component({
  selector: 'app-graphs-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphExplorer, GraphDetail],
  templateUrl: './graphs-page.html',
  styleUrl: './graphs-page.css',
})
export class GraphsPage {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  /** The `graphId` route param, or `null` on the bare `/graphs` list route. Reads
   * off `paramMap` (not an `@Input`) so both routes below can share this one
   * component while staying param-driven and refresh-safe. */
  protected readonly graphId = toSignal(this.route.paramMap.pipe(map((params) => params.get('graphId'))), {
    initialValue: null,
  });

  protected select(graphId: string): void {
    void this.router.navigate(['/graphs', graphId]);
  }
}

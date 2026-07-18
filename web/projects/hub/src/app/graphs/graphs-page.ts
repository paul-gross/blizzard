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
  template: `
    <div class="layout">
      <fleet-graph-explorer class="explorer" [selectedGraphId]="graphId()" (selectGraph)="select($event)" />
      @if (graphId(); as id) {
        <fleet-graph-detail class="detail" [graphId]="id" />
      } @else {
        <div class="placeholder" data-testid="graphs-page-placeholder">
          <p>Select a graph to view its structure.</p>
        </div>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .layout {
      height: 100%;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(260px, 380px) 1fr;
      gap: 6px;
      padding: 6px;
    }
    .explorer {
      min-height: 0;
      overflow-y: auto;
    }
    .detail {
      min-height: 0;
      min-width: 0;
    }
    .placeholder {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--label);
      font-family: var(--mono);
      font-size: var(--fs-lg);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      border: 1px solid var(--bezel);
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
    }
  `,
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

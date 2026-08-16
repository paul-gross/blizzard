import { ChangeDetectionStrategy, Component } from '@angular/core';
import { LocalPanel } from 'local-panel';

/**
 * The `/board` route (issue #313) — today's machine-local panel, unchanged
 * beyond the fact log's move to {@link EventsPage}. A thin route wrapper
 * around {@link LocalPanel}, mirroring the hub's own page components: the
 * route owns nothing beyond mounting it.
 */
@Component({
  selector: 'app-board-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanel],
  template: `<local-panel />`,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
  `,
})
export class BoardPage {}

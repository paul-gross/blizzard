import { ChangeDetectionStrategy, Component } from '@angular/core';
import { KitPanel } from 'fleet';
import { FactLog } from 'local-panel';

/**
 * The `/events` route (issue #313) — the local fact log at full width,
 * split out of the panel layout's right rail so it gets the whole viewport
 * rather than a rail-sized panel. {@link FactLog} is self-fetching
 * (`status.query.ts`'s shared dashboard read), so this page owns nothing
 * beyond mounting it in a full-height panel, mirroring the hub's own
 * `events-page.ts`.
 */
@Component({
  selector: 'app-events-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FactLog, KitPanel],
  template: `
    <div class="layout">
      <fleet-kit-panel class="feed" label="local fact log">
        <local-fact-log />
      </fleet-kit-panel>
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
      padding: 6px;
      display: flex;
    }
    .feed {
      flex: 1;
      min-height: 0;
      min-width: 0;
      overflow-y: auto;
    }
  `,
})
export class EventsPage {}

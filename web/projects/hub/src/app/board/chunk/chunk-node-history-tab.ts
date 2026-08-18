import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { ChunkTimeline, type hubApi } from 'fleet';

/**
 * The chunk detail page's Node history tab — the same {@link ChunkTimeline} the desktop
 * dock and this page's own General tab render, but here with row activation turned on:
 * a row picked writes its own join key back to the URL ({@link selectedKey} is that raw
 * `?step` param, round-tripped the same way the Artifacts tab's `?artifact` is).
 *
 * Presentational, no injected query — the whole render is a pure function of
 * {@link detail}, already loaded by {@link ChunkPage}. A per-step artifact/transcript
 * body is not this component's own yet; it composes only the timeline until the
 * separately-fetched transcript index gives it something else to show.
 */
@Component({
  selector: 'app-chunk-node-history-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkTimeline],
  template: `
    <div class="nh-tab" data-testid="chunk-node-history-tab">
      <fleet-chunk-detail-timeline
        [detail]="detail()"
        [heading]="false"
        [activatable]="true"
        [selectedKey]="selectedKey()"
        (selectStep)="selectStep.emit($event)"
      />
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .nh-tab {
      height: 100%;
      min-height: 0;
      overflow-y: auto;
    }
  `,
})
export class ChunkNodeHistoryTab {
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The raw `?step` URL param — the requested selection, forwarded straight to
   * {@link ChunkTimeline} with no lookup against the timeline's own rows here. */
  readonly selectedKey = input<string | null>(null);

  /** Emitted with a row's join key when the operator activates it. */
  readonly selectStep = output<string>();
}

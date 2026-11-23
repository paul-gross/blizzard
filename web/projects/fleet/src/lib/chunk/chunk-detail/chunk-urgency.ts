import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import type { ChunkDetail } from '../chunk-page/chunk-detail.model';
import type { Urgency } from '../../core/urgency';
import { isHexColor, legibleOnDark, projectColorValue } from '../../kit/kit-swatch-color';
import type { ChunkControl } from './chunk-control';

@Component({
  selector: 'fleet-chunk-urgency',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-urgency.html',
  styleUrl: './chunk-urgency.css',
})
export class ChunkUrgency {
  readonly detail = input.required<Pick<ChunkDetail, 'chunk_id' | 'terminal' | 'urgency'>>();
  readonly control = input<ChunkControl | null>(null);
  readonly banner = input(false);
  readonly bannerLayout = input<'desktop' | 'mobile'>('desktop');
  readonly resolvedUrgency = input<Urgency | null>(null);
  /** The banner's image mark, read by the host; shown only in banner mode when the urgency has no emoji. */
  readonly iconUrl = input<string | null>(null);
  protected urgencyColor(color: string): string {
    return isHexColor(color) ? legibleOnDark(color) : projectColorValue(color);
  }
  protected readonly selected = computed(() => this.control()?.urgency?.values().find((u) => u.id === this.detail().urgency?.urgency_id) ?? this.resolvedUrgency());
}

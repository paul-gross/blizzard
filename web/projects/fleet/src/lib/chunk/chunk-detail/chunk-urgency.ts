import { ChangeDetectionStrategy, Component, computed, inject, InjectionToken, input, signal, type Signal } from '@angular/core';
import type { ChunkDetail, UrgencyView } from '../../api/hub';
import { isHexColor, legibleOnDark, projectColorValue } from '../../kit/kit-swatch-color';
import type { ChunkControl } from './chunk-control';

/** The host supplies authenticated icon reads; a daemon without them renders no image. */
export const CHUNK_URGENCY_ICON = new InjectionToken<(icon: () => { key: string; digest: string } | null) => Signal<string | null>>('chunk urgency icon', {
  factory: () => () => signal(null),
});

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
  readonly resolvedUrgency = input<UrgencyView | null>(null);
  protected urgencyColor(color: string): string {
    return isHexColor(color) ? legibleOnDark(color) : projectColorValue(color);
  }
  protected readonly selected = computed(() => this.control()?.urgency?.values().find((u) => u.urgency_id === this.detail().urgency?.urgency_id) ?? this.resolvedUrgency());
  protected readonly iconUrl = inject(CHUNK_URGENCY_ICON)(() => {
    const u = this.selected();
    return this.banner() && u && !u.emoji && u.icon ? { key: u.urgency_id, digest: u.icon.digest } : null;
  });
}

import { ChangeDetectionStrategy, Component, computed, inject, InjectionToken, input, signal, type Signal } from '@angular/core';
import type { ChunkDetail } from '../../api/hub';
import { KitMenu, KitMenuPanel } from '../../kit/kit-menu';
import { KitMenuItemRadio } from '../../kit/kit-menu-item';
import { KitUrgencyPill } from '../../kit/kit-urgency-pill';
import type { ChunkControl } from './chunk-control';

/** The host supplies authenticated icon reads; a daemon without them renders no image. */
export const CHUNK_URGENCY_ICON = new InjectionToken<(icon: () => { key: string; digest: string } | null) => Signal<string | null>>('chunk urgency icon', {
  factory: () => () => signal(null),
});

@Component({
  selector: 'fleet-chunk-urgency',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMenu, KitMenuPanel, KitMenuItemRadio, KitUrgencyPill],
  templateUrl: './chunk-urgency.html',
})
export class ChunkUrgency {
  readonly detail = input.required<ChunkDetail>();
  readonly control = input<ChunkControl | null>(null);
  protected readonly selectedId = computed(() => this.detail().terminal
    ? this.detail().urgency?.urgency_id ?? null
    : this.control()?.urgency?.pending(this.detail().chunk_id) ?? this.detail().urgency?.urgency_id ?? null);

  protected readonly selected = computed(() => this.control()?.urgency?.values().find((u) => u.urgency_id === this.selectedId()) ?? null);
  protected readonly options = computed(() => (this.control()?.urgency?.values() ?? []).filter((u) => !u.retired).sort((a, b) => (a.place ?? Infinity) - (b.place ?? Infinity)));
  protected readonly iconUrl = inject(CHUNK_URGENCY_ICON)(() => {
    const u = this.selected();
    return u && !u.emoji && u.icon ? { key: u.urgency_id, digest: u.icon.digest } : null;
  });
  protected choose(id: string): void {
    const control = this.control();
    if (!this.detail().terminal && control?.canControl() && !control.urgency?.pending(this.detail().chunk_id) && id !== this.selectedId()) {
      control.urgency?.set(this.detail().chunk_id, id);
    }
  }
}

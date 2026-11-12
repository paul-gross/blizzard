import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { isHexColor, legibleOnDark, projectColorValue } from './kit-swatch-color';
import { KitTooltip } from './kit-tooltip';

/** A named urgency with its configured color and optional mark. */
@Component({
  selector: 'fleet-kit-urgency-pill',
  imports: [KitTooltip],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-urgency-pill.html',
  styleUrl: './kit-urgency-pill.css',
})
export class KitUrgencyPill {
  readonly name = input.required<string>();
  readonly color = input.required<string>();
  readonly emoji = input<string | null>(null);
  readonly iconUrl = input<string | null>(null);
  readonly description = input<string | null>(null);
  readonly focusable = input(true);
  protected readonly tip = computed(() => [this.name(), this.description()].filter(Boolean).join(' — '));
  protected readonly ink = computed(() => {
    const color = this.color();
    return isHexColor(color) ? legibleOnDark(color) : projectColorValue(color);
  });
}

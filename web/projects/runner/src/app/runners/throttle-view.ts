import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitAsyncState, KitButton, type KitAsyncStateValue, KitErrorLine, KitFactList, type KitFact } from 'fleet/kit';
import { KitConfirmDialog } from 'fleet/kit/dialog';

import type { Throttle } from '../core/throttle.domain';
import { throttleLines, throttleSummary } from '../core/throttle.model';

@Component({
  selector: 'app-throttle-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitConfirmDialog, KitErrorLine, KitFactList],
  templateUrl: './throttle-view.html',
})
export class ThrottleView {
  readonly throttle = input<Throttle | null>(null);
  readonly state = input.required<KitAsyncStateValue>();
  readonly pending = input(false);
  readonly error = input<string | null>(null);
  readonly scope = input<string | null>(null);
  readonly scopeReady = input(false);
  readonly clearFloor = output<void>();
  protected readonly confirming = signal(false);
  protected readonly lines = computed(() => this.throttle() ? throttleLines(this.throttle()!) : []);
  protected readonly rows = computed<readonly KitFact[]>(() => this.lines().map((line) => ({ label: line.label, value: line.text })));
  protected readonly summary = computed(() => this.throttle() ? throttleSummary(this.throttle()!) : '');
  protected readonly prompt = computed(() => this.throttle()?.floor.subscription
    ? `Clear ${this.throttle()!.floor.subscription}'s floor for ${this.scope()}?`
    : `Clear ${this.scope()}’s floor?`);
}

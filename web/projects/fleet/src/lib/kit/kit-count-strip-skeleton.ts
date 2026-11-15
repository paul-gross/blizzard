import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitSkeleton } from './kit-skeleton';

/** A count strip's placeholder, with five cells by default. */
@Component({
  selector: 'fleet-kit-count-strip-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton],
  templateUrl: './kit-count-strip-skeleton.html',
  styleUrl: './kit-count-strip-skeleton.css',
})
export class KitCountStripSkeleton {
  readonly cells = input(5);
  readonly captions = input<readonly string[]>([]);
  readonly counts = input<readonly string[]>([]);
  protected readonly indexes = computed(() => Array.from({ length: this.cells() }, (_, i) => i));
  protected caption(index: number): string { return this.captions()[index] ?? 'M'; }
  protected count(index: number): string { return this.counts()[index] ?? '0'; }
  protected readonly columns = computed(() => `repeat(${this.cells()}, minmax(0, 1fr))`);
}

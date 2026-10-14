import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { Router } from '@angular/router';
import { KitChip } from 'fleet';

import { injectActiveRunner } from '../../core/active-runner';
import { injectHostRunnersQuery } from '../../host/host.query';
import { type RunnerChoice, runnerChoices } from './runner-switcher.model';

/**
 * The runner switcher — one chip per runner on the host, the active runner's selected,
 * each braked one naming its brake. Choosing a runner opens its board; a halted runner
 * is offered but cannot be chosen. It sits in the desktop tab strip ({@link AppNav}) and,
 * on mobile, under the titlebar (`variant="mobile"`).
 *
 * The chips scroll inside the control rather than widen the row, so a host with many
 * runners never pushes the strip past the viewport.
 */
@Component({
  selector: 'app-runner-switcher',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip],
  templateUrl: './runner-switcher.html',
  styleUrl: './runner-switcher.css',
  host: { '[class.mobile]': "variant() === 'mobile'" },
})
export class RunnerSwitcher {
  /** Where the switcher sits — inline in the desktop tab strip, or as its own mobile row. */
  readonly variant = input<'inline' | 'mobile'>('inline');

  private readonly router = inject(Router);
  private readonly runnersQuery = injectHostRunnersQuery();

  protected readonly active = injectActiveRunner();

  protected readonly choices = computed(() => runnerChoices(this.runnersQuery.data() ?? []));

  protected choose(choice: RunnerChoice): void {
    if (!choice.openable || choice.runnerId === this.active()) return;
    void this.router.navigate(['/runners', choice.runnerId, 'board']);
  }
}

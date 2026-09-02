import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { RoutineView, ScopeView } from '../api/hub';

/**
 * The findings triage bucket's routine/scope picker — presentational only, no
 * query injection, `routine-list.ts`'s own shape: renders the routines/scopes it
 * is handed and emits whichever was picked, `<select>` value as a plain string
 * (`routine-list.ts`'s own `routinePick`/native-`select`-collision-avoidance
 * naming). The container owns what picking one means — resetting the other half's
 * explicit pin, or the class/state filters — `bzh:frontend-container-presentational`.
 */
@Component({
  selector: 'fleet-routine-scope-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  templateUrl: './routine-scope-picker.html',
  styleUrl: './routine-scope-picker.css',
})
export class FleetRoutineScopePicker {
  readonly routines = input.required<readonly RoutineView[]>();
  readonly scopes = input.required<readonly ScopeView[]>();
  readonly selectedRoutine = input<string | null>(null);
  readonly selectedScope = input<string | null>(null);

  readonly routinePick = output<string>();
  readonly scopePick = output<string>();

  protected onRoutineChange(event: Event): void {
    this.routinePick.emit((event.target as HTMLSelectElement).value);
  }

  protected onScopeChange(event: Event): void {
    this.scopePick.emit((event.target as HTMLSelectElement).value);
  }
}

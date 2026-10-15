// exemplar: bzh:frontend-container-presentational, bzh:frontend-kit-floor — inputs and outputs only, chrome from the kit.

import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { type KitAsyncStateValue, PermitVerb, FleetWhen, KitAsyncState, KitButton, KitDialog, KitPanel, KitTextInput } from 'fleet';
import { LensLink } from 'fleet/shell';
import { type PermitRow, permitProjectLabel } from './permits-page.model';
import type { PermitMove, PermitMoveVars, PermitRevokeVars } from './permits.mutations';

const MOVE_LABEL: Readonly<Record<PermitMove, string>> = { grant: 'Grant', suspend: 'Suspend', resume: 'Resume' };

/** A panel of permits, one row each, offering each row the verbs its permit names as legal. */
@Component({
  selector: 'app-permits-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitButton, KitDialog, KitPanel, KitTextInput, LensLink, RouterLink],
  templateUrl: './permits-view.html',
})
export class PermitsView {
  /** The permits to list, each in the state it shows. */
  readonly rows = input<readonly PermitRow[]>([]);
  /** Whether the rows are loading, failed, empty or ready. */
  readonly state = input.required<KitAsyncStateValue>();
  /** The panel's label. */
  readonly label = input('Permits');
  /** Whether the verb controls show; without it the rows are read-only. */
  readonly canEdit = input(false);
  /** The last refused verb's message, or `null`. */
  readonly actionError = input<string | null>(null);

  /** Fired with the permit and verb a row's move control was pressed for. */
  readonly move = output<PermitMoveVars>();
  /** Fired with the permit and note a revocation was confirmed with. */
  readonly revoke = output<PermitRevokeVars>();

  /** The permit a revocation note is being written for. */
  protected readonly revoking = signal<string | null>(null);
  protected readonly note = signal('');

  protected moves(row: PermitRow): PermitMove[] {
    return row.permit.verbs.filter((verb): verb is PermitMove => verb !== PermitVerb.REVOKE);
  }

  protected canRevoke(row: PermitRow): boolean {
    return row.permit.verbs.includes(PermitVerb.REVOKE);
  }

  protected moveLabel(verb: PermitMove): string {
    return MOVE_LABEL[verb];
  }

  protected projectLabel(row: PermitRow): string {
    return permitProjectLabel(row.permit);
  }

  protected onRevokeStart(permitId: string): void {
    this.note.set('');
    this.revoking.set(permitId);
  }

  protected onRevokeConfirm(permitId: string): void {
    this.revoking.set(null);
    this.revoke.emit({ permitId, note: this.note() });
  }
}

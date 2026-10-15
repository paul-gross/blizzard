// exemplar: bzh:frontend-container-presentational, bzh:frontend-pending-override — a lensed list, moved row by row.

import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';

import { type KitAsyncStateValue, asyncState, errorMessage, injectPendingMutationVariables } from 'fleet';
import { permitMoveMutationKey, permitRevokeMutationKey } from '../core/mutation-keys';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { injectQueryFilters } from '../core/route-state';
import { HOLDER_PARAM, type PermitRow, permitsLabel, withPendingPermitChanges } from './permits-page.model';
import {
  type PermitMoveVars,
  type PermitRevokeVars,
  injectPermitMoveMutation,
  injectPermitRevokeMutation,
} from './permits.mutations';
import { injectPermitsQuery } from './permits.query';
import { PermitsView } from './permits-view';

/** `/permits` — the lensed project's permits, narrowed by `?holder=`, each movable from its own row. */
@Component({
  selector: 'app-permits-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PermitsView],
  templateUrl: './permits-page.html',
})
export class PermitsPage {
  private readonly filters = injectQueryFilters();
  private readonly holder = computed(() => this.filters.read(HOLDER_PARAM));
  private readonly permitsQuery = injectPermitsQuery(() => this.holder());
  private readonly meQuery = injectMeQuery();

  private readonly moveMutation = injectPermitMoveMutation();
  private readonly revokeMutation = injectPermitRevokeMutation();
  private readonly pendingMoves = injectPendingMutationVariables<PermitMoveVars>(permitMoveMutationKey);
  private readonly pendingRevokes = injectPendingMutationVariables<PermitRevokeVars>(permitRevokeMutationKey);

  protected readonly rows = computed<readonly PermitRow[]>(() =>
    withPendingPermitChanges(this.permitsQuery.data() ?? [], this.pendingMoves(), this.pendingRevokes()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.permitsQuery, this.rows().length === 0),
  );
  protected readonly label = computed(() => permitsLabel(this.holder()));
  /** Every verb takes `config:edit`; a viewer is offered none. */
  protected readonly canEdit = computed(() => hasPermission(this.meQuery.data(), 'config:edit'));
  /** The last refused verb's message; cleared when the next verb starts. */
  protected readonly actionError = signal<string | null>(null);

  protected onMove(vars: PermitMoveVars): void {
    this.actionError.set(null);
    this.moveMutation.mutate(vars, {
      onError: (error) => this.actionError.set(errorMessage(error, `Could not ${vars.verb} the permit.`)),
    });
  }

  protected onRevoke(vars: PermitRevokeVars): void {
    this.actionError.set(null);
    this.revokeMutation.mutate(vars, {
      onError: (error) => this.actionError.set(errorMessage(error, 'Could not revoke the permit.')),
    });
  }
}

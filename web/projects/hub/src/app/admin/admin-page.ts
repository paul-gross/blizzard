import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { UsersTable } from './users-table';
import { asyncState, injectPendingMutationVariables, KitAsyncState } from 'fleet';
import { injectAssignRoleMutation, type AssignRoleVars } from './assign-role.mutations';
import { injectMeQuery } from '../core/auth/me.query';
import { injectUsersQuery } from './users.query';
import { assignRoleMutationKey } from '../core/mutation-keys';
import { assignRoleErrorText, pendingRoleUserIds } from './admin-page.model';

/**
 * The `/admin` route — a container reading `injectUsersQuery()` (`GET /api/users`)
 * and `injectMeQuery()` (the signed-in actor's own identity, for `isSelf` gating and the
 * `assignable_roles` the selectors offer in {@link UsersTable}; each user row carries its own
 * actor-relative `assignable_roles`), composing the presentational table
 * (`bzh:frontend-container-presentational`).
 * Routed behind the `user:manage` nav gate (`app-nav.ts`'s `showAdmin`); this page's own `GET /api/users` read
 * is refused (`403`) hub-side below that permission regardless of the nav gate, so a
 * direct navigation renders that as its own error state rather than a silent stub.
 *
 * Under `auth.mode = "none"` there are no users to administer — the nav entry itself
 * is hidden (no `user:manage` gate reads meaningfully with the implicit
 * operator/superuser), so this route is unreachable through normal navigation; a
 * direct hit still renders (the API answers an empty list — no users to list).
 *
 * A role change the hub refuses (`RoleAssignmentRefused`, 403 — self-change,
 * `superuser` grant/revoke, or an `admin` actor touching the `admin` tier) is
 * this page's own error state (`assignRoleError()`): the mutation
 * already rejects on a non-2xx response (`assign-role.mutations.ts`), so
 * without reading `assignRoleMutation.error()` here a refusal would fail
 * silently — the table keeps whatever the `<select>` was last set to, reading
 * as a success that only a reload reveals never happened.
 */
@Component({
  selector: 'app-admin-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [UsersTable, KitAsyncState],
  templateUrl: './admin-page.html',
  styleUrl: './admin-page.css',
})
export class AdminPage {
  protected readonly usersQuery = injectUsersQuery();
  private readonly meQuery = injectMeQuery();
  private readonly assignRoleMutation = injectAssignRoleMutation();

  private readonly pendingAssignments = injectPendingMutationVariables<AssignRoleVars>(assignRoleMutationKey);

  /** The user ids a role assignment is in flight for — each row's select disables on its own id. */
  protected readonly pendingUserIds = computed(() => pendingRoleUserIds(this.pendingAssignments()));

  protected readonly currentUserId = computed(() => this.meQuery.data()?.user_id ?? null);
  protected readonly assignableRoles = computed(() => this.meQuery.data()?.assignable_roles ?? []);

  /** `KitAsyncState`'s own triad — `empty` is never reached here (an empty user list
   * still renders {@link UsersTable}'s own empty state, a distinct message from "no
   * users administrable at all"), so this collapses to loading/error/ready. */
  protected readonly triadState = computed(() => asyncState(this.usersQuery, false));

  /** The last {@link assignRoleMutation} refusal's own `detail` (the hub's
   * `RoleAssignmentRefused` message), or `null` once a fresh `mutate()` call
   * clears the mutation's error state. */
  protected readonly assignRoleError = computed<string | null>(() => assignRoleErrorText(this.assignRoleMutation.error()));

  protected onAssignRole(vars: { userId: string; role: string }): void {
    this.assignRoleMutation.mutate(vars);
  }
}

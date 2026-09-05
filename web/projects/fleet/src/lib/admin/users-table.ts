import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { UserView } from '../api/hub';
import { KitAsyncState } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';
import { FleetWhen } from '../when-display';

/** The four roles ever assignable through this page's own mutation — `superuser` is
 * bootstrap-only (never offered as a select option, `hub/auth/service.py`'s own
 * `assign_role` refuses it outright). */
const ASSIGNABLE_ROLES: readonly string[] = ['pending', 'guest', 'contributor', 'admin'];

/**
 * The admin page's user table (issue #94; `pending` added by issue #210) —
 * presentational: renders `users()` with a role selector per row, gated by the two
 * hub-side rules a `superuser`-tiered actor clears and an `admin`-tiered one does not
 * (`AuthService.assign_role`'s own rules, mirrored here so a disabled control never
 * invites a refused request rather than catching the 403 after the fact):
 *
 * - a row naming the signed-in actor (`currentUserId()`) renders its role as plain
 *   text, not a selector — self-role-change is refused;
 * - a row already `superuser` renders its role as plain text too — `superuser` is
 *   bootstrap-only, never touched through this page;
 * - every other row's selector offers `pending`/`guest`/`contributor`/`admin` (a
 *   `pending` row selects and behaves exactly like any other non-`superuser` row —
 *   it is not treated as static); the `admin` option is disabled, and the whole
 *   selector is disabled when the row is *already* `admin`, unless `isSuperuser()` —
 *   only a `superuser` actor may grant or revoke `admin`.
 *
 * A `403` the mutation still surfaces despite this (a stale permission between page
 * load and submit) is the container's own error state, not this component's concern.
 */
@Component({
  selector: 'fleet-users-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, FleetWhen],
  templateUrl: './users-table.html',
  styleUrl: './users-table.css',
})
export class UsersTable {
  /** `GET /api/users`'s own rows. */
  readonly users = input<readonly UserView[]>([]);

  /** The signed-in actor's own `user_id` — the row it names renders read-only
   * (self-role-change is refused hub-side). */
  readonly currentUserId = input<string | null>(null);

  /** Whether the signed-in actor holds `superuser` — the only tier that may grant or
   * revoke `admin` through this page. */
  readonly isSuperuser = input(false);

  /** Fired with `{userId, role}` when a row's selector picks a new role. */
  readonly assignRole = output<{ userId: string; role: string }>();

  protected readonly assignableRoles = ASSIGNABLE_ROLES;

  protected isSelf(user: UserView): boolean {
    return user.user_id === this.currentUserId();
  }

  protected onRoleChange(userId: string, event: Event): void {
    const role = (event.target as HTMLSelectElement).value;
    this.assignRole.emit({ userId, role });
  }
}

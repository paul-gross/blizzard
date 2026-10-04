import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type hubApi, type UserView, KitAsyncState, KitPanel, FleetWhen } from 'fleet';

/**
 * The admin page's user table — presentational: renders `users()` with a role selector per row,
 * gated by what the wire says the signed-in actor may do, so a disabled control never invites a
 * refused request rather than catching the 403 after the fact:
 *
 * - a row naming the signed-in actor (`currentUserId()`) renders its role as plain
 *   text, not a selector, as does a row whose role is not one the API assigns at all
 *   (outside {@link assignableRoles});
 * - every other row's selector offers {@link assignableRoles}, enabling only the roles in
 *   the row's own `assignable_roles` and disabling the whole selector when that list is
 *   empty (an `admin` row before a non-`superuser` actor).
 *
 * A `403` the mutation still surfaces despite this (a stale permission between page
 * load and submit) is not this component's concern.
 */
@Component({
  selector: 'app-users-table',
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

  /** The roles a row's selector offers — `GET /api/me`'s `assignable_roles`, every role the
   * role-assignment API may grant. */
  readonly assignableRoles = input<readonly hubApi.Role[]>([]);

  /** Fired with `{userId, role}` when a row's selector picks a new role. */
  readonly assignRole = output<{ userId: string; role: string }>();

  protected isSelf(user: UserView): boolean {
    return user.user_id === this.currentUserId();
  }

  protected isAssignableRole(role: hubApi.Role): boolean {
    return this.assignableRoles().includes(role);
  }

  protected onRoleChange(userId: string, event: Event): void {
    const role = (event.target as HTMLSelectElement).value;
    this.assignRole.emit({ userId, role });
  }
}

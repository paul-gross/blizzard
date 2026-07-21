import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { UserView } from '../api/hub';
import { KitPanel } from '../kit/kit-panel';

/** The three roles ever assignable through this page's own mutation — `superuser` is
 * bootstrap-only (never offered as a select option, `hub/auth/service.py`'s own
 * `assign_role` refuses it outright). */
const ASSIGNABLE_ROLES: readonly string[] = ['guest', 'contributor', 'admin'];

/**
 * The admin page's user table (issue #94) — presentational: renders `users()` with a
 * role selector per row, gated by the two hub-side rules a `superuser`-tiered actor
 * clears and an `admin`-tiered one does not (`AuthService.assign_role`'s own rules,
 * mirrored here so a disabled control never invites a refused request rather than
 * catching the 403 after the fact):
 *
 * - a row naming the signed-in actor (`currentUserId()`) renders its role as plain
 *   text, not a selector — self-role-change is refused;
 * - a row already `superuser` renders its role as plain text too — `superuser` is
 *   bootstrap-only, never touched through this page;
 * - every other row's selector offers `guest`/`contributor`/`admin`; the `admin`
 *   option is disabled, and the whole selector is disabled when the row is *already*
 *   `admin`, unless `isSuperuser()` — only a `superuser` actor may grant or revoke
 *   `admin`.
 *
 * A `403` the mutation still surfaces despite this (a stale permission between page
 * load and submit) is the container's own error state, not this component's concern.
 */
@Component({
  selector: 'fleet-users-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel],
  template: `
    <fleet-kit-panel class="users-table" aria-label="Users" data-testid="users-table" label="Users">
      @if (users().length === 0) {
        <p class="none" data-testid="users-table-empty">No users yet.</p>
      } @else {
        <table>
          <thead>
            <tr>
              <th>Username</th>
              <th>Display name</th>
              <th>Email</th>
              <th>Identities</th>
              <th>Role</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            @for (user of users(); track user.user_id) {
              <tr data-testid="users-table-row" [attr.data-user-id]="user.user_id">
                <td data-testid="users-table-username">{{ user.username }}</td>
                <td data-testid="users-table-display-name">{{ user.display_name }}</td>
                <td data-testid="users-table-email">{{ user.email ?? '—' }}</td>
                <td data-testid="users-table-identities">
                  {{ user.identities?.length ? user.identities!.map((i) => i.provider_name).join(', ') : '—' }}
                </td>
                <td>
                  @if (isSelf(user) || user.role === 'superuser') {
                    <span data-testid="users-table-role-static">{{ user.role }}{{ isSelf(user) ? ' (you)' : '' }}</span>
                  } @else {
                    <select
                      data-testid="users-table-role-select"
                      [value]="user.role"
                      [disabled]="user.role === 'admin' && !isSuperuser()"
                      (change)="onRoleChange(user.user_id, $event)"
                    >
                      @for (role of assignableRoles; track role) {
                        <option [value]="role" [disabled]="role === 'admin' && !isSuperuser()">{{ role }}</option>
                      }
                    </select>
                  }
                </td>
                <td data-testid="users-table-created-at">{{ user.created_at }}</td>
              </tr>
            }
          </tbody>
        </table>
      }
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .users-table {
      min-height: 0;
      overflow-y: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: var(--fs-sm);
    }
    th {
      text-align: left;
      color: var(--label);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-size: var(--fs-label);
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
    }
    td {
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
    }
    select {
      font-family: inherit;
      background: var(--overlay-30);
      border: 1px solid var(--line);
      color: var(--text);
    }
    .none {
      color: var(--label-dim);
      padding: 12px;
    }
  `,
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

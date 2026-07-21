import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import {
  UsersTable,
  injectAssignRoleMutation,
  injectMeQuery,
  injectUsersQuery,
  KitAsyncState,
  type KitAsyncStateValue,
} from 'fleet';

/**
 * The `/admin` route (issue #94) — the real admin page replacing #93's stub: a
 * container reading `injectUsersQuery()` (`GET /api/users`) and `injectMeQuery()`
 * (the signed-in actor's own identity, for `isSelf`/`isSuperuser` gating in
 * {@link UsersTable} — `isSuperuser` reads `me().role`, not a permission: `superuser`
 * and `admin` share one permission bundle server-side, `auth_core/__init__.py`'s own
 * "the one thing `superuser` can do that `admin` cannot is a per-action rule, not a
 * distinct permission bit"), composing the presentational table
 * (`bzh:frontend-container-presentational`). Routed behind the `user:manage` nav gate
 * landed in #93 (`app-nav.ts`'s `showAdmin`); this page's own `GET /api/users` read
 * is refused (`403`) hub-side below that permission regardless of the nav gate, so a
 * direct navigation renders that as its own error state rather than a silent stub.
 *
 * Under `auth.mode = "none"` there are no users to administer — the nav entry itself
 * is hidden (no `user:manage` gate reads meaningfully with the implicit
 * operator/superuser), so this route is unreachable through normal navigation; a
 * direct hit still renders (the API answers an empty list — no users to list).
 */
@Component({
  selector: 'app-admin-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [UsersTable, KitAsyncState],
  template: `
    <fleet-kit-async-state
      [state]="triadState()"
      loadingText="Loading users…"
      loadingTestid="admin-page-loading"
      errorText="Failed to load users."
      errorTestid="admin-page-error"
    >
      <fleet-users-table
        [users]="usersQuery.data() ?? []"
        [currentUserId]="currentUserId()"
        [isSuperuser]="isSuperuser()"
        (assignRole)="onAssignRole($event)"
      />
    </fleet-kit-async-state>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
      padding: 6px;
      position: relative;
    }
  `,
})
export class AdminPage {
  protected readonly usersQuery = injectUsersQuery();
  private readonly meQuery = injectMeQuery();
  private readonly assignRoleMutation = injectAssignRoleMutation();

  protected readonly currentUserId = computed(() => this.meQuery.data()?.user_id ?? null);
  protected readonly isSuperuser = computed(() => this.meQuery.data()?.role === 'superuser');

  /** `KitAsyncState`'s own triad — `empty` is never reached here (an empty user list
   * still renders {@link UsersTable}'s own empty state, a distinct message from "no
   * users administrable at all"), so this collapses to loading/error/ready. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.usersQuery.isPending()) return 'loading';
    if (this.usersQuery.isError()) return 'error';
    return 'ready';
  });

  protected onAssignRole(vars: { userId: string; role: string }): void {
    this.assignRoleMutation.mutate(vars);
  }
}

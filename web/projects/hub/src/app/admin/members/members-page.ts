import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { type hubApi, type MemberView, asyncState, injectPendingMutationVariables, KitAsyncState, KitConfirmDialog } from 'fleet';
import { injectMeQuery } from '../../core/auth/me.query';
import { TenantContext } from '../../core/auth/tenant-context';
import { memberRoleMutationKey, revokeMemberMutationKey } from '../../core/mutation-keys';
import { MembersTable } from './members-table';
import { injectMembersQuery } from './members.query';
import {
  type MemberRoleVars,
  type RevokeMemberVars,
  injectMemberRoleMutation,
  injectRevokeMemberMutation,
} from './members.mutations';
import { memberChangeErrorText, membersLabel, pendingMemberIds, revokePrompt, tenantNameOf } from './members-page.model';

/**
 * The `/admin/members` route — the members of the tenant the board acts in. A container composing the presentational
 * {@link MembersTable} (`bzh:frontend-container-presentational`) from `GET /api/members`, `/api/me` (the actor's own
 * id, so their row stays read-only) and the selected tenant's name, which labels the page.
 *
 * Every rule about who may change whom is the hub's: each row carries the roles the actor may give it and whether it
 * may be revoked, and the table offers only those. A change the hub still refuses (a stale listing, the tenant's last
 * admin) is this page's own error line — without it a refused change would read as a success that only a reload
 * reveals never happened. A revocation asks first.
 *
 * Routed behind the `user:manage` nav gate (`app.ts`'s `canManageUsers`); the hub refuses the listing (`403`) below
 * that permission whatever the nav shows, so a direct navigation renders that as the error state.
 */
@Component({
  selector: 'app-members-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MembersTable, KitAsyncState, KitConfirmDialog],
  templateUrl: './members-page.html',
  styleUrl: './members-page.css',
})
export class MembersPage {
  protected readonly membersQuery = injectMembersQuery();
  private readonly meQuery = injectMeQuery();
  private readonly tenant = inject(TenantContext);
  private readonly roleMutation = injectMemberRoleMutation();
  private readonly revokeMutation = injectRevokeMemberMutation();

  private readonly pendingRoles = injectPendingMutationVariables<MemberRoleVars>(memberRoleMutationKey);
  private readonly pendingRevokes = injectPendingMutationVariables<RevokeMemberVars>(revokeMemberMutationKey);

  /** The user ids a membership change is in flight for — each row's controls disable on their own id. */
  protected readonly pendingUserIds = computed(() => pendingMemberIds(this.pendingRoles(), this.pendingRevokes()));

  protected readonly currentUserId = computed(() => this.meQuery.data()?.user_id ?? null);

  /** The selected tenant's name, as the header shows it. */
  private readonly tenantName = computed(() => tenantNameOf(this.tenant.resolver()(this.meQuery.data()?.tenants ?? [])));

  protected readonly label = computed(() => membersLabel(this.tenantName()));

  /** `KitAsyncState`'s triad — `empty` is never reached here: an empty listing renders the table's own empty state. */
  protected readonly triadState = computed(() => asyncState(this.membersQuery, false));

  /** The last refused change's own `detail`, or `null`. Starting either change clears the other's error, so the line
   * always speaks of the latest attempt. */
  protected readonly changeError = computed<string | null>(() =>
    memberChangeErrorText(this.roleMutation.error() ?? this.revokeMutation.error()),
  );

  /** The member a revocation is waiting on confirmation for. */
  protected readonly revoking = signal<MemberView | null>(null);

  protected readonly revokeConfirm = computed(() => revokePrompt(this.revoking(), this.tenantName()));

  protected onChangeRole(vars: { userId: string; role: hubApi.Role }): void {
    this.revokeMutation.reset();
    this.roleMutation.mutate(vars);
  }

  protected onRevoke(member: MemberView): void {
    this.revoking.set(member);
  }

  protected onRevokeConfirmed(): void {
    const member = this.revoking();
    this.revoking.set(null);
    if (member === null) return;
    this.roleMutation.reset();
    this.revokeMutation.mutate({ userId: member.user_id });
  }

  protected onRevokeCancelled(): void {
    this.revoking.set(null);
  }
}

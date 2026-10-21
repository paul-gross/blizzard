import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';

import { type hubApi, type MemberView, asyncState, injectPendingMutationVariables, KitAsyncState, KitConfirmDialog, KitErrorLine } from 'fleet';
import { injectMeQuery } from '../../core/auth/me.query';
import { memberRoleMutationKey, revokeMemberMutationKey } from '../../core/mutation-keys';
import { MembersTable } from './members-table';
import { injectMembersQuery } from './members.query';
import {
  type MemberRoleVars,
  type RevokeMemberVars,
  injectMemberRoleMutation,
  injectRevokeMemberMutation,
} from './members.mutations';
import { memberChangeErrorText, pendingMemberIds, revokePrompt } from './members-page.model';
import { LensNote } from '../../core/lens-note';

/**
 * The `/admin/members` route — the members of the tenant the board acts in. A container composing the presentational
 * {@link MembersTable} (`bzh:frontend-container-presentational`) from `GET /api/members`, `/api/me` (the actor's own
 * id, so their row stays read-only) and the selected tenant's name, which labels the page.
 *
 * A change the hub refuses is this page's own error line (`members-page.spec.ts`). A revocation asks first.
 */
@Component({
  selector: 'app-members-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MembersTable, KitAsyncState, KitConfirmDialog, KitErrorLine, LensNote],
  templateUrl: './members-page.html',
  styleUrl: './members-page.css',
})
export class MembersPage {
  protected readonly membersQuery = injectMembersQuery();
  private readonly meQuery = injectMeQuery();
  private readonly roleMutation = injectMemberRoleMutation();
  private readonly revokeMutation = injectRevokeMemberMutation();

  private readonly pendingRoles = injectPendingMutationVariables<MemberRoleVars>(memberRoleMutationKey);
  private readonly pendingRevokes = injectPendingMutationVariables<RevokeMemberVars>(revokeMemberMutationKey);

  /** The user ids a membership change is in flight for — each row's controls disable on their own id. */
  protected readonly pendingUserIds = computed(() => pendingMemberIds(this.pendingRoles(), this.pendingRevokes()));

  protected readonly currentUserId = computed(() => this.meQuery.data()?.user_id ?? null);

  /** `KitAsyncState`'s triad — `empty` is never reached here: an empty listing renders the table's own empty state. */
  protected readonly triadState = computed(() => asyncState(this.membersQuery, false));

  /** The last refused change's own `detail`, or `null`. Starting either change clears the other's error, so the line
   * always speaks of the latest attempt. */
  protected readonly changeError = computed<string | null>(() =>
    memberChangeErrorText(this.roleMutation.error() ?? this.revokeMutation.error()),
  );

  /** The member a revocation is waiting on confirmation for. */
  protected readonly revoking = signal<MemberView | null>(null);

  protected readonly revokeConfirm = computed(() => revokePrompt(this.revoking()));

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

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type hubApi, type KitChipOption, type MemberView, FleetWhen, KitAsyncState, KitButton, KitPanel, KitSelect } from 'fleet';
import { canChangeRole, canRevoke, roleChoices } from './members-page.model';

/**
 * The members page's table — presentational: one row per member of the board's tenant, with a role selector and a
 * revoke button gated by what the hub says the signed-in actor may do to that row (`assignable_roles`, `revocable`), so
 * a control is never offered for a change the hub would refuse:
 *
 * - the actor's own row (`currentUserId()`) shows its role as plain text and no revoke button;
 * - a row whose role the actor may not change (no other role offered) shows its role as plain text;
 * - a row the hub marks `revocable` has a revoke button.
 *
 * A member who is also a hub administrator carries a `hub admin` tag beside their username.
 */
@Component({
  selector: 'app-members-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitPanel, KitSelect, FleetWhen],
  templateUrl: './members-table.html',
  styleUrl: './members-table.css',
})
export class MembersTable {
  /** `GET /api/members`'s rows. */
  readonly members = input<readonly MemberView[]>([]);

  /** The panel's label — names the tenant the members belong to. */
  readonly label = input('Members');

  /** The signed-in actor's own `user_id` — their row renders read-only (the hub refuses changing your own). */
  readonly currentUserId = input<string | null>(null);

  /** The user ids a membership change is in flight for; each row's controls disable on its own id. */
  readonly pendingUserIds = input<readonly string[]>([]);

  /** Fired with `{userId, role}` when a row's selector picks another role. */
  readonly changeRole = output<{ userId: string; role: hubApi.Role }>();

  /** Fired with the member whose revoke button was pressed. */
  readonly revoke = output<MemberView>();

  protected canChangeRole(member: MemberView): boolean {
    return canChangeRole(member, this.currentUserId());
  }

  protected canRevoke(member: MemberView): boolean {
    return canRevoke(member, this.currentUserId());
  }

  protected isSelf(member: MemberView): boolean {
    return member.user_id === this.currentUserId();
  }

  protected isPending(member: MemberView): boolean {
    return this.pendingUserIds().includes(member.user_id);
  }

  protected roleOptions(member: MemberView): readonly KitChipOption[] {
    return roleChoices(member).map((role) => ({ value: role, label: role }));
  }

  protected onRoleChange(member: MemberView, role: string): void {
    const chosen = roleChoices(member).find((r) => r === role);
    if (chosen !== undefined && chosen !== member.role) this.changeRole.emit({ userId: member.user_id, role: chosen });
  }
}

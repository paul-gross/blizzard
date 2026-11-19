import { type Member, type MemberRole, canChangeRole, canRevoke, isOwnMember, memberRoleLabel } from './members.domain';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type KitChipOption, type KitAsyncStateValue, FleetWhen, KitAsyncState, KitBadge, KitButton, KitGrid, KitGridCell, KitGridRow, KitGridWidget, KitPanel, KitSelect } from 'fleet';
import { roleChoices } from './members-page.model';

/**
 * The members page's table — presentational: one row per member of the board's tenant, with a role selector and a
 * revoke button gated by the row's `assignableRoles` and `revocable`:
 *
 * - the actor's own row (`currentUserId()`) shows its role as plain text and no revoke button;
 * - a row whose role the actor may not change (no other role offered) shows its role as plain text;
 * - a row the hub marks `revocable` has a revoke button.
 *
 * A member who is also a hub administrator carries a `hub admin` tag beside their username.
 *
 * The body is a kit grid: the whole table is one Tab stop, the arrow keys move between cells, and Enter or Space works
 * a cell's role select or revoke button. A pending row's disabled controls, and a row's empty revoke cell, are skipped.
 */
@Component({
  selector: 'app-members-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitGrid, KitGridCell, KitGridRow, KitGridWidget, KitPanel, KitSelect, FleetWhen],
  templateUrl: './members-table.html',
  styleUrl: './members-table.css',
})
export class MembersTable {
  /** The query state governing the table body. */
  readonly state = input.required<KitAsyncStateValue>();

  /** `GET /api/members`'s rows. */
  readonly members = input<readonly Member[]>([]);

  /** The panel's label — names the tenant the members belong to. */
  readonly label = input('Members');

  /** The signed-in actor's own `userId` — their row renders read-only (the hub refuses changing your own). */
  readonly currentUserId = input<string | null>(null);

  /** The user ids a membership change is in flight for; each row's controls disable on its own id. */
  readonly pendingUserIds = input<readonly string[]>([]);

  /** Fired with `{userId, role}` when a row's selector picks another role. */
  readonly changeRole = output<{ userId: string; role: MemberRole }>();

  /** Fired with the member whose revoke button was pressed. */
  readonly revoke = output<Member>();

  protected canChangeRole(member: Member): boolean {
    return canChangeRole(member, this.currentUserId());
  }

  protected canRevoke(member: Member): boolean {
    return canRevoke(member, this.currentUserId());
  }

  protected isSelf(member: Member): boolean {
    return isOwnMember(member, this.currentUserId());
  }

  protected isPending(member: Member): boolean {
    return this.pendingUserIds().includes(member.userId);
  }

  protected roleOptions(member: Member): readonly KitChipOption[] {
    const options = roleChoices(member).map((role) => ({ value: role, label: role }));
    return typeof member.role === 'string' ? options : [{ value: member.role.unlisted, label: member.role.unlisted }, ...options];
  }

  protected roleLabel = memberRoleLabel;

  protected onRoleChange(member: Member, role: string): void {
    const chosen = roleChoices(member).find((r) => r === role);
    if (chosen !== undefined && chosen !== member.role) this.changeRole.emit({ userId: member.userId, role: chosen });
  }
}

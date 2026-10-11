import { computed, type Signal } from '@angular/core';
import type { MemberView } from 'fleet';

import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { injectMembersQuery } from './members/members.query';

/** Who made a change, as a person reads it — a member's name for their user id. */
export type ActorName = (actor: string) => string;

/** The actor as recorded, for a caller with no members to name it by. */
export const RAW_ACTOR: ActorName = (actor) => actor;

/** Names each actor by the member holding that user id — the display name, else the username. An actor no member holds
 * (a removed member, the hub itself) stays as recorded. */
export function actorNameOf(members: readonly MemberView[] | undefined): ActorName {
  if (!Array.isArray(members) || members.length === 0) return RAW_ACTOR;
  const names = new Map(members.map((member) => [member.user_id, member.display_name || member.username]));
  return (actor) => names.get(actor) || actor;
}

/** The tenant's members as the namer of every recorded actor. The members read is held back without `user:manage`
 * (the hub refuses it), and until it answers each actor shows as recorded. */
export function injectActorName(): Signal<ActorName> {
  const me = injectMeQuery();
  const members = injectMembersQuery(() => hasPermission(me.data(), 'user:manage'));
  return computed(() => actorNameOf(members.data()));
}

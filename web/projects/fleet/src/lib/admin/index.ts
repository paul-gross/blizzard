/*
 * `admin/`'s sub-barrel (issue #94, `bzh:frontend-disjoint-diffs`) — the admin page
 * feature's public surface, re-exported one line from the root `public-api.ts`.
 */

export { injectUsersQuery } from './users.query';
export { injectAssignRoleMutation } from './assign-role.mutation';
export { UsersTable } from './users-table';
export type { UserView, UserIdentityView } from '../api/hub';

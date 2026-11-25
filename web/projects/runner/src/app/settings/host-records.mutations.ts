import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { hostRecordMutationKey } from '../core/mutation-keys';
import { hostSettingsChangesKey, hostSubscriptionsKey, hostWorkspacesKey } from '../core/query-keys';
import type { HostRecordKind } from './host-records.domain';
import type { RecordCreate, RecordEdit } from './host-records.translate';

/** A new record, its body its create request. */
export type RecordCreateVars = RecordCreate;

/** A sparse edit of the record `key`, sent against the revision the edit started from. */
export type RecordEditVars = RecordEdit & { readonly key: string; readonly revision: number };

/** A retire or an enable of the `kind` record `key`. */
export interface RecordLifecycleVars {
  readonly kind: HostRecordKind;
  readonly key: string;
}

/** What a write to a `kind` record re-reads once it settles: every read of that kind, and the host's change log,
 * which carries the record's history. */
export function recordInvalidations(kind: HostRecordKind): readonly (readonly unknown[])[] {
  return [kind === 'workspace' ? hostWorkspacesKey : hostSubscriptionsKey, hostSettingsChangesKey];
}

function settle(queryClient: QueryClient, kind: HostRecordKind): Promise<unknown> {
  return Promise.all(recordInvalidations(kind).map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

async function create(vars: RecordCreateVars): Promise<void> {
  const { error } =
    vars.kind === 'workspace'
      ? await runnerApi.createWorkspaceApiHostWorkspacesPost({ body: vars.body, throwOnError: false })
      : await runnerApi.createSubscriptionApiHostSubscriptionsPost({ body: vars.body, throwOnError: false });
  if (error) throw error;
}

async function edit(vars: RecordEditVars): Promise<void> {
  const headers = { 'if-match': String(vars.revision) };
  const { error } =
    vars.kind === 'workspace'
      ? await runnerApi.editWorkspaceApiHostWorkspacesWorkspaceIdPatch({
          path: { workspace_id: vars.key },
          headers,
          body: vars.body,
          throwOnError: false,
        })
      : await runnerApi.editSubscriptionApiHostSubscriptionsSlugPatch({
          path: { slug: vars.key },
          headers,
          body: vars.body,
          throwOnError: false,
        });
  if (error) throw error;
}

async function retire(vars: RecordLifecycleVars): Promise<void> {
  const { error } =
    vars.kind === 'workspace'
      ? await runnerApi.retireWorkspaceApiHostWorkspacesWorkspaceIdRetirePost({ path: { workspace_id: vars.key }, throwOnError: false })
      : await runnerApi.retireSubscriptionApiHostSubscriptionsSlugRetirePost({ path: { slug: vars.key }, throwOnError: false });
  if (error) throw error;
}

async function enable(vars: RecordLifecycleVars): Promise<void> {
  const { error } =
    vars.kind === 'workspace'
      ? await runnerApi.enableWorkspaceApiHostWorkspacesWorkspaceIdEnablePost({ path: { workspace_id: vars.key }, throwOnError: false })
      : await runnerApi.enableSubscriptionApiHostSubscriptionsSlugEnablePost({ path: { slug: vars.key }, throwOnError: false });
  if (error) throw error;
}

/** `POST /api/host/workspaces` or `POST /api/host/subscriptions`. Settles once {@link recordInvalidations} have
 * refetched; it writes nothing to the cache. */
export function injectRecordCreateMutation(kind: () => HostRecordKind) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostRecordMutationKey(kind(), 'create'),
    mutationFn: create,
    onSettled: (_data: void | undefined, _error: unknown, vars: RecordCreateVars) => settle(queryClient, vars.kind),
  }));
}

/** `PATCH /api/host/workspaces/{workspace_id}` or `PATCH /api/host/subscriptions/{slug}` with `If-Match:
 * <revision>`, so an edit against a stale read is refused with a 409. Settles once {@link recordInvalidations} have
 * refetched. */
export function injectRecordEditMutation(kind: () => HostRecordKind) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostRecordMutationKey(kind(), 'edit'),
    mutationFn: edit,
    onSettled: (_data: void | undefined, _error: unknown, vars: RecordEditVars) => settle(queryClient, vars.kind),
  }));
}

/** `POST …/{key}/retire` on a workspace or a subscription. Settles once {@link recordInvalidations} have refetched. */
export function injectRecordRetireMutation(kind: () => HostRecordKind) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostRecordMutationKey(kind(), 'retire'),
    mutationFn: retire,
    onSettled: (_data: void | undefined, _error: unknown, vars: RecordLifecycleVars) => settle(queryClient, vars.kind),
  }));
}

/** `POST …/{key}/enable` on a workspace or a subscription. Settles once {@link recordInvalidations} have refetched. */
export function injectRecordEnableMutation(kind: () => HostRecordKind) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: hostRecordMutationKey(kind(), 'enable'),
    mutationFn: enable,
    onSettled: (_data: void | undefined, _error: unknown, vars: RecordLifecycleVars) => settle(queryClient, vars.kind),
  }));
}

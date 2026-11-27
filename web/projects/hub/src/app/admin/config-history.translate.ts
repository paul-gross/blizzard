import { RecordKind, type ConfigChangesPage, type ConfigChangeView } from 'fleet/hub-api';
import type { ConfigChange, ConfigChangePage, ConfigRecordKind } from './config-history.domain';

export function toConfigChange(change: ConfigChangeView): ConfigChange {
  const { id, revision, actor, actor_name, apply_id, door, op, recorded_at, record_kind, record_key, record_label } = change;
  return {
    id,
    revision,
    actor,
    actor_name,
    apply_id,
    door,
    op,
    recorded_at,
    record_kind,
    record_key,
    record_label,
    diff: change.diff.map((field) => ({ ...field })),
  };
}

export function toConfigChangePage(page: ConfigChangesPage): ConfigChangePage {
  return { changes: page.changes.map(toConfigChange), next_before: page.next_before, total: page.total };
}

/** Convert a domain history filter at the request boundary. */
export function toWireRecordKind(kind: ConfigRecordKind): RecordKind {
  const kinds: Record<ConfigRecordKind, RecordKind> = {
    work_source: RecordKind.WORK_SOURCE,
    secret: RecordKind.SECRET,
    repository: RecordKind.REPOSITORY,
    forge: RecordKind.FORGE,
    credential: RecordKind.CREDENTIAL,
    scope: RecordKind.SCOPE,
    routine: RecordKind.ROUTINE,
    cadence: RecordKind.CADENCE,
    project: RecordKind.PROJECT,
    project_source_link: RecordKind.PROJECT_SOURCE_LINK,
    project_repository_link: RecordKind.PROJECT_REPOSITORY_LINK,
    urgency: RecordKind.URGENCY,
    urgency_order: RecordKind.URGENCY_ORDER,
    tenant_settings: RecordKind.TENANT_SETTINGS,
  };
  return kinds[kind];
}

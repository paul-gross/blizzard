import type { ConfigChangesPage, ConfigChangeView } from 'fleet';
import type { ConfigChange, ConfigChangePage } from './config-history.domain';

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

import type { ConfigChangeView } from 'fleet';
import type { ConfigChange } from './config-history.domain';

export function toConfigChange(change: ConfigChangeView): ConfigChange {
  const { id, revision, actor, actor_name, door, op, recorded_at } = change;
  return {
    id,
    revision,
    actor,
    actor_name,
    door,
    op,
    recorded_at,
    diff: change.diff.map((field) => ({ ...field })),
  };
}

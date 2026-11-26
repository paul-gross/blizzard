import type { WorkSourceSummary } from 'fleet/hub-api';
import type { ProjectWorkSource } from './work-sources.domain';

export function toProjectWorkSource(
  source: WorkSourceSummary,
): ProjectWorkSource {
  const { work_source_id, slug, name, provider, locator, retired, built_in } =
    source;
  return { work_source_id, slug, name, provider, locator, retired, built_in };
}

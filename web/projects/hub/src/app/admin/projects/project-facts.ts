import type { TemplateRef } from '@angular/core';
import type { ChunkCountsView, KitFact, ProjectView } from 'fleet';

import { queueText } from './project-overview.model';

/** What the overview's facts read besides the project itself. */
export interface ProjectFactsInput {
  /** The slugs the project left, newest first. */
  readonly former: readonly string[];
  /** The project's chunk counts, once read. */
  readonly counts: ChunkCountsView | undefined;
  /** Renders when it was created. */
  readonly created: TemplateRef<unknown> | undefined;
  /** Renders its color: the swatch and the palette name. */
  readonly color: TemplateRef<unknown> | undefined;
  /** Renders its revision: who last changed it, through which door. */
  readonly revision: TemplateRef<unknown> | undefined;
}

/** The project's facts: its slug and the slugs it left, its color, when it was made, its queue, its revision and
 * its id. A fact whose read has not answered is left out rather than shown empty. */
export function projectFacts(project: ProjectView | null, input: ProjectFactsInput): readonly KitFact[] {
  if (project === null) return [];
  const facts: KitFact[] = [];
  facts.push({ label: 'Slug', value: project.slug, testid: 'admin-projects-slug' });
  if (input.former.length > 0) {
    facts.push({ label: 'Former slugs', value: input.former.join(', '), testid: 'admin-projects-former-slugs' });
  }
  if (input.color) facts.push({ label: 'Color', template: input.color, testid: 'admin-projects-color' });
  if (input.created) facts.push({ label: 'Created', template: input.created, testid: 'admin-projects-created' });
  if (input.counts) facts.push({ label: 'Queue', value: queueText(input.counts), testid: 'admin-projects-queue' });
  if (input.revision) facts.push({ label: 'Revision', template: input.revision, testid: 'admin-projects-revision' });
  facts.push({ label: 'Id', value: project.project_id, testid: 'admin-projects-id' });
  return facts;
}

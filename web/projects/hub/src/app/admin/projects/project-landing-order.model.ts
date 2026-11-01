import { moveItemInArray } from '@angular/cdk/drag-drop';
import type { LandingPositionRequest, RepositoryLinkView } from 'fleet';

import type { ProjectLinkRowVm } from './projects.model';

/** The two sections of a project's landing order. */
export type LandingSection = 'ranked' | 'unranked';

/** A project's repository rows split by whether they hold a landing position, each in the hub's order. */
export interface LandingSections {
  readonly ranked: readonly ProjectLinkRowVm[];
  readonly unranked: readonly ProjectLinkRowVm[];
}

/** A drop as cdk reports it: the dragged repository, the section and index it left, and where it landed. */
export interface LandingDrop {
  readonly key: string;
  readonly from: LandingSection;
  readonly previousIndex: number;
  readonly to: LandingSection;
  readonly currentIndex: number;
}

/** One landing-position write — the request against one project. */
export interface LandingPositionVars {
  readonly projectId: string;
  readonly body: LandingPositionRequest;
}

/**
 * The rows split into ranked and unranked by their link's `position`. The hub already lists ranked links by position,
 * then unranked ones by slug, so each section keeps the rows' order.
 */
export function landingSections(rows: readonly ProjectLinkRowVm[], links: readonly RepositoryLinkView[]): LandingSections {
  const ranked = new Set(links.filter((link) => link.position !== null).map((link) => link.repository_id));
  return {
    ranked: rows.filter((row) => ranked.has(row.key)),
    unranked: rows.filter((row) => !ranked.has(row.key)),
  };
}

/**
 * A drop resolved to the one position it asks the hub for. Landing in the ranked section places the repository after
 * its new predecessor (`null` at the front), replaying a move within the section on a copy (cdk moves nothing itself);
 * landing in the unranked section from the ranked one unranks it. A move within the unranked section, or a drop that
 * changed nothing, resolves to `null`.
 */
export function resolveLandingDrop(sections: LandingSections, drop: LandingDrop): LandingPositionRequest | null {
  if (drop.to === 'unranked') return drop.from === 'ranked' ? { repository: drop.key, unranked: true } : null;
  const order = sections.ranked.map((row) => row.key);
  if (drop.from === 'ranked') {
    if (drop.previousIndex === drop.currentIndex) return null;
    moveItemInArray(order, drop.previousIndex, drop.currentIndex);
  } else {
    order.splice(drop.currentIndex, 0, drop.key);
  }
  return { repository: drop.key, after_repository: drop.currentIndex === 0 ? null : order[drop.currentIndex - 1] };
}

/** `sections` with one requested position applied: the row leaves where it sat and lands where it asked to. */
function withPosition(sections: LandingSections, body: LandingPositionRequest): LandingSections {
  const row = [...sections.ranked, ...sections.unranked].find((candidate) => candidate.key === body.repository);
  if (!row) return sections;
  const ranked = sections.ranked.filter((candidate) => candidate !== row);
  const unranked = sections.unranked.filter((candidate) => candidate !== row);
  if (body.unranked) {
    // The hub lists unranked links by slug, so the moved row waits where it will read back.
    const at = unranked.findIndex((candidate) => candidate.name > row.name);
    unranked.splice(at === -1 ? unranked.length : at, 0, row);
    return { ranked, unranked };
  }
  const anchor = body.after_repository ?? null;
  const at = anchor === null ? 0 : ranked.findIndex((candidate) => candidate.key === anchor) + 1;
  ranked.splice(at === 0 && anchor !== null ? ranked.length : at, 0, row);
  return { ranked, unranked };
}

/** `sections` with each of this project's pending position writes folded in, oldest first, so a move renders before it settles. */
export function foldLandingPositions(
  sections: LandingSections,
  pending: readonly LandingPositionVars[],
  projectId: string | null,
): LandingSections {
  return pending.filter((vars) => vars.projectId === projectId).reduce((folded, vars) => withPosition(folded, vars.body), sections);
}

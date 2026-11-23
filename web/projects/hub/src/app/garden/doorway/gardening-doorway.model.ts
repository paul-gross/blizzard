import type { Project } from '../../core/project.domain';
import type { Cadence } from '../core/cadence.domain';
import type { Routine } from '../core/routine.domain';
import type { Proposal } from '../core/proposal.domain';
import { lensedUrl } from '../../core/project-lens';
import { GARDENING_DOORWAY_URL, GARDENING_LANDING_TAB } from '../core/garden-project';
import { isGardenProposalWaiting } from '../proposals/gardening-proposals-page.model';

/** A project's doorway card, built from the tenant-wide reads. */
export interface DoorwayCard {
  readonly projectId: string;
  readonly slug: string;
  readonly name: string;
  readonly color: string;
  readonly counts: string;
  readonly href: string;
}

function countLabel(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

export function doorwayCards(
  projects: readonly Project[],
  cadences: readonly Cadence[],
  routines: readonly Routine[],
  proposals: readonly Proposal[],
): readonly DoorwayCard[] {
  return [...projects].sort((a, b) => a.name.localeCompare(b.name)).map((project) => {
    const cadenceCount = cadences.filter((item) => item.projectId === project.project_id).length;
    const routineCount = routines.filter((item) => item.projectId === project.project_id).length;
    const proposalCount = proposals.filter((item) => item.projectId === project.project_id && isGardenProposalWaiting(item)).length;
    const counts = [
      ...(cadenceCount > 0 ? [countLabel(cadenceCount, 'cadence')] : []),
      ...(routineCount > 0 ? [countLabel(routineCount, 'routine')] : []),
      countLabel(proposalCount, 'pending proposal'),
    ].join(' · ');
    return {
      projectId: project.project_id,
      slug: project.slug,
      name: project.name,
      color: project.color,
      counts,
      href: lensedUrl(`${GARDENING_DOORWAY_URL}/${GARDENING_LANDING_TAB}`, project.slug),
    };
  });
}

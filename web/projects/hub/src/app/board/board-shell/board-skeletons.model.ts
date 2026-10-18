import { DONE_LANE, LANES } from 'fleet';

/** The most card skeletons a loading lane may show — a lane absent here shows up to 4. BACKLOG and
 * DONE run longest in a working fleet, so they stand in with more. */
const LANE_SKELETON_MAX: Readonly<Record<string, number>> = { notready: 10, [DONE_LANE]: 20 };

/** The fewest and most row skeletons the loading list shows. */
const LIST_SKELETON_MIN = 6;
const LIST_SKELETON_MAX = 14;

/** A random count of card skeletons for each lane, 0 up to its {@link LANE_SKELETON_MAX} inclusive,
 * so a loading board has the uneven look of a real one rather than a uniform block. */
export function laneSkeletonCounts(random: () => number): ReadonlyMap<string, number> {
  return new Map(LANES.map((lane) => [lane.key, Math.floor(random() * ((LANE_SKELETON_MAX[lane.key] ?? 4) + 1))]));
}

/** A random count of row skeletons for the loading list, {@link LIST_SKELETON_MIN} up to
 * {@link LIST_SKELETON_MAX} inclusive. */
export function listSkeletonCount(random: () => number): number {
  return LIST_SKELETON_MIN + Math.floor(random() * (LIST_SKELETON_MAX - LIST_SKELETON_MIN + 1));
}

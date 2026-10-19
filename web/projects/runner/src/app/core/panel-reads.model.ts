import type { runnerApi } from 'fleet';

/** The runner a merged item came from — what its actions and links target. */
export interface RunnerOwner {
  readonly runnerId: string;
  /** Its name on the host — what the Runner filter names it by. */
  readonly name: string;
  /** Its display name — the bare runner name, the host being the one the panel is served from. */
  readonly label: string;
}

/** An item read off one runner, carrying that runner and the project it is attributed to
 * (`null` where no project can be told). */
export type Owned<T> = T & { readonly owner: RunnerOwner; readonly projectSlug: string | null };

export type PanelLease = Owned<runnerApi.LeaseView>;
export type PanelFact = Owned<runnerApi.FactView>;
export type PanelAsk = Owned<runnerApi.AskView>;

/** One runner's dashboard, beside the runner it came from. */
export interface RunnerDashboard {
  readonly owner: RunnerOwner;
  readonly dashboard: runnerApi.DashboardView;
}

/** A project the host serves. `entries` are the binding entries and project ids that name
 * it — the host's environments name it by its id. */
export interface PanelProject {
  readonly slug: string;
  readonly name: string;
  readonly entries: readonly string[];
  /** The project's colour, as the hub names it — `null` for a binding no served project accounts
   * for, or a runner whose hub predates project colours. */
  readonly color: string | null;
}

/**
 * The colour a project's chunks are tinted with, or `null` to render them untinted — the one
 * place the panel resolves a project's colour, as the runner's dashboard carries it from the hub.
 */
export function projectColorFor(project: PanelProject | null | undefined): string | null {
  return project?.color ?? null;
}

/** Each runner's projects by runner id. */
export type RunnerProjects = ReadonlyMap<string, readonly PanelProject[]>;

/** The serving runners on the host that have an id to read under, in wire order — each labeled by its bare name, since
 * every runner the panel shows runs on the one host it is served from. */
export function servingOwners(runners: readonly runnerApi.HostRunnerView[]): readonly RunnerOwner[] {
  return runners.flatMap((runner) =>
    runner.state === 'serving' && runner.runner_id !== null
      ? [{ runnerId: runner.runner_id, name: runner.name, label: runner.name }]
      : [],
  );
}

/** `owners` narrowed to the runner named `name`, every one for `null`. */
export function scopedOwners(owners: readonly RunnerOwner[], name: string | null): readonly RunnerOwner[] {
  return name === null ? owners : owners.filter((owner) => owner.name === name);
}

/**
 * One runner's projects: each project its dashboard reports served under a slug, then each
 * of its host bindings no served project accounts for, under the binding's own entry —
 * the runner reports no other name for it.
 */
export function runnerProjects(
  dashboard: runnerApi.DashboardView | undefined,
  runner: runnerApi.HostRunnerView | undefined,
): readonly PanelProject[] {
  const projects = new Map<string, { slug: string; name: string; entries: Set<string>; color: string | null }>();
  const covered = new Set<string>();
  for (const served of dashboard?.runner?.served_projects ?? []) {
    if (!served.slug) continue;
    covered.add(served.entry);
    const project = projects.get(served.slug) ?? { slug: served.slug, name: served.name ?? served.slug, entries: new Set(), color: served.color ?? null };
    project.entries.add(served.entry);
    if (served.project_id) project.entries.add(served.project_id);
    projects.set(served.slug, project);
  }
  for (const binding of runner?.bindings ?? []) {
    if (covered.has(binding.project) || projects.has(binding.project)) continue;
    projects.set(binding.project, { slug: binding.project, name: binding.project, entries: new Set([binding.project]), color: null });
  }
  return [...projects.values()].map((project) => ({ ...project, entries: [...project.entries] }));
}

/** Every project across `byRunner`, one per slug, by name. */
export function projectCatalog(byRunner: RunnerProjects): readonly PanelProject[] {
  const merged = new Map<string, PanelProject>();
  for (const projects of byRunner.values()) {
    for (const project of projects) {
      const seen = merged.get(project.slug);
      merged.set(
        project.slug,
        seen ? { ...seen, entries: [...new Set([...seen.entries, ...project.entries])] } : project,
      );
    }
  }
  return [...merged.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** The slug of the project among `projects` that `entry` names, by slug, binding entry, or id. */
export function slugForEntry(projects: readonly PanelProject[], entry: string): string | null {
  return projects.find((project) => project.slug === entry || project.entries.includes(entry))?.slug ?? null;
}

/** Whether `projects` includes `slug` — always for `null`, All. */
export function servesProject(projects: readonly PanelProject[], slug: string | null): boolean {
  return slug === null || projects.some((project) => project.slug === slug);
}

/** The project a runner's item belongs to when nothing on the item says: the runner's only project, else unknown. */
function soleProject(projects: readonly PanelProject[]): string | null {
  return projects.length === 1 ? projects[0].slug : null;
}

/**
 * The project `lease` is attributed to. A lease carries no project, so it is told from the
 * environment it holds — while that environment still holds the lease's own chunk — and
 * otherwise from its runner serving a single project. `null` where neither says.
 */
export function leaseProject(
  lease: runnerApi.LeaseView,
  projects: readonly PanelProject[],
  environments: ReadonlyMap<string, runnerApi.HostEnvironmentView>,
): string | null {
  const env = lease.environment_id ? environments.get(lease.environment_id) : undefined;
  if (env?.project && env.chunk_id === lease.chunk_id) return slugForEntry(projects, env.project) ?? env.project;
  return soleProject(projects);
}

/** The host's environments by id. */
export function environmentsById(
  workspaces: readonly runnerApi.HostWorkspaceEnvironmentsView[],
): ReadonlyMap<string, runnerApi.HostEnvironmentView> {
  return new Map(workspaces.flatMap((workspace) => (workspace.items ?? []).map((env) => [env.environment_id, env] as const)));
}

/** Leases read off several runners as one list: live leases first, then newest first, so
 * the first lease seen per chunk is its freshest attempt. */
export function mergeLeases(perRunner: readonly (readonly PanelLease[])[]): readonly PanelLease[] {
  return perRunner.flat().sort((a, b) => {
    const live = Number(b.state !== 'closed') - Number(a.state !== 'closed');
    return live !== 0 ? live : b.created_at.localeCompare(a.created_at);
  });
}

/** A lookup from a runner's chunk to the project its leases are attributed to. */
export function chunkProjectLookup(leases: readonly PanelLease[]): (runnerId: string, chunkId: string | null | undefined) => string | null {
  const projects = new Map<string, string | null>();
  for (const lease of leases) {
    const key = `${lease.owner.runnerId} ${lease.chunk_id}`;
    if (!projects.has(key) || projects.get(key) === null) projects.set(key, lease.projectSlug);
  }
  return (runnerId, chunkId) => (chunkId ? (projects.get(`${runnerId} ${chunkId}`) ?? null) : null);
}

/** Owns each of `items`, read off `owner`, attributing each through its chunk, else the runner's only project. */
export function ownByChunk<T extends { readonly chunk_id?: string | null }>(
  items: readonly T[],
  owner: RunnerOwner,
  projects: readonly PanelProject[],
  chunkProject: (runnerId: string, chunkId: string | null | undefined) => string | null,
): Owned<T>[] {
  return items.map((item) => ({
    ...item,
    owner,
    projectSlug: chunkProject(owner.runnerId, item.chunk_id) ?? soleProject(projects),
  }));
}

/** `items` narrowed to the project `slug`, every item for `null`. */
export function inProject<T extends { readonly projectSlug: string | null }>(items: readonly T[], slug: string | null): readonly T[] {
  return slug === null ? items : items.filter((item) => item.projectSlug === slug);
}

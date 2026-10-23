import type { runnerApi } from 'fleet/runner-shell';

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

/** The project `slug` names among `projects`, by name — the slug itself for one `projects` does not
 * name, `null` for no project. */
export function projectNameFor(projects: readonly PanelProject[], slug: string | null): string | null {
  return slug === null ? null : (projects.find((project) => project.slug === slug)?.name ?? slug);
}

/** Each runner's projects by runner id. */
export type RunnerProjects = ReadonlyMap<string, readonly PanelProject[]>;

/** The runners on the host whose routes answer and that have an id to read under, in wire order — each labeled by its bare name, since
 * every runner the panel shows runs on the one host it is served from. */
export function servingOwners(runners: readonly runnerApi.HostRunnerView[]): readonly RunnerOwner[] {
  return runners.flatMap((runner) =>
    runner.served && runner.runner_id !== null
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

/** The slug of the project an item's wire `project_id` names among `projects`, the id itself when none resolves it,
 * `null` when the item names none. */
export function attributedProject(item: { readonly project_id?: string | null }, projects: readonly PanelProject[]): string | null {
  const id = item.project_id;
  return id ? (slugForEntry(projects, id) ?? id) : null;
}

/** Leases read off several runners as one list: live leases first, then newest first, so
 * the first lease seen per chunk is its freshest attempt. */
export function mergeLeases(perRunner: readonly (readonly PanelLease[])[]): readonly PanelLease[] {
  return perRunner.flat().sort((a, b) => {
    const live = Number(b.state !== 'closed') - Number(a.state !== 'closed');
    return live !== 0 ? live : b.created_at.localeCompare(a.created_at);
  });
}

/** Owns each of `items`, read off `owner`, attributing each by its own wire `project_id` among `owner`'s
 * `projects` ({@link attributedProject}). */
export function ownByProject<T extends { readonly project_id?: string | null }>(
  items: readonly T[],
  owner: RunnerOwner,
  projects: readonly PanelProject[],
): Owned<T>[] {
  return items.map((item) => ({ ...item, owner, projectSlug: attributedProject(item, projects) }));
}

/** `items` narrowed to the project `slug`, every item for `null`. */
export function inProject<T extends { readonly projectSlug: string | null }>(items: readonly T[], slug: string | null): readonly T[] {
  return slug === null ? items : items.filter((item) => item.projectSlug === slug);
}

/** Each runner's dashboard by runner id. */
export function dashboardsByRunner(dashboards: readonly RunnerDashboard[]): ReadonlyMap<string, runnerApi.DashboardView> {
  return new Map(dashboards.map((entry) => [entry.owner.runnerId, entry.dashboard] as const));
}

/** Whether any of `dashboards` reports its runner reaching its hub — a malformed body reads as not reaching it. */
export function anyHubReachable(dashboards: readonly RunnerDashboard[]): boolean {
  return dashboards.some(({ dashboard }) => dashboard.runner?.hub?.reachable ?? false);
}

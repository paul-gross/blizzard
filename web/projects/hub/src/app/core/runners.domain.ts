import type { HostConnection, SubscriptionUsage, UnenforceableGate, UnknownHostValue } from 'fleet';

/** Runner facts used by project presentation, including the hub's claimability judgment. */
export interface ProjectRunner {
  readonly runner_id: string;
  readonly runner_name: string;
  readonly connection: string;
  readonly online: boolean;
  readonly projects: readonly ProjectRunnerAssignment[];
}

export interface ProjectRunnerAssignment {
  readonly project_id: string | null;
  readonly claimable: boolean;
}

/** Live runners whose hub-resolved assignment can claim work for this project. */
export function servingProjectRunners<Runner extends Pick<ProjectRunner, 'online' | 'projects'>>(
  projectId: string,
  runners: readonly Runner[],
): readonly Runner[] {
  return runners.filter(
    (runner) =>
      runner.online &&
      runner.projects.some(
        (project) => project.project_id === projectId && project.claimable,
      ),
  );
}

/** A host's membership as the fleet-id join reads it: its id and the runners it lists. */
export interface HostMembership {
  readonly host_id: string;
  readonly runner_ids?: readonly string[];
}

/** The id the Fleet tab selects a runner by: its host's id when a host lists the runner, else the runner's own. */
export function fleetIdOf(runnerId: string, hosts: readonly HostMembership[]): string {
  return hosts.find((host) => host.runner_ids?.includes(runnerId))?.host_id ?? runnerId;
}

export type KnownVersionStanding = 'in_window' | 'unversioned' | 'refused';
/** The hub's judgement of a runner's version against its window; `UnknownHostValue` carries one this build cannot name. */
export type VersionStanding = KnownVersionStanding | UnknownHostValue;

export type KnownVersionRefusal = 'too-old' | 'too-new' | 'unversioned';
export type VersionRefusal = KnownVersionRefusal | UnknownHostValue;

export type KnownDeclaredLever = 'floor' | 'pace' | 'utilization' | 'budget' | 'schedule';
export type DeclaredLeverKind = KnownDeclaredLever | UnknownHostValue;

/** One project entry of a runner's declaration, as the hub resolved it; `claimable` is the hub's judgement. */
export interface ServedProject {
  readonly entry: string;
  readonly project_id: string | null;
  readonly slug: string | null;
  readonly name: string | null;
  readonly color: string | null;
  readonly claimable: boolean;
}

/** One project-workspace binding a hosted runner declares. */
export interface RunnerBinding {
  readonly project: string;
  readonly workspace_id: string;
  readonly agents: number;
  readonly workspace_unreported: boolean;
}

/** One harness binding a runner reported, with its own health check's verdict. */
export interface RunnerCapabilityFact {
  readonly harness_id: string;
  readonly available: boolean;
  readonly default: boolean;
  readonly version: string | null;
  readonly tiers: readonly string[];
  readonly supported_models: readonly string[];
}

/** One reason a throttle holds a runner back, as the runner reported it. */
export interface RunnerDeclining {
  readonly lever: string;
  readonly lever_kind: DeclaredLeverKind;
  readonly detail: string;
  readonly subscription: string | null;
  readonly until: string | null;
}

/** One runner of the hub's registry; `online`, `claimable`, `version_standing` and `declining` are the hub's judgements. */
export interface HubRunner {
  readonly runner_id: string;
  readonly runner_name: string;
  readonly host_id: string | null;
  readonly workspace_id: string | null;
  readonly connection: HostConnection;
  readonly online: boolean;
  readonly last_seen_at: string | null;
  readonly added_at: string;
  readonly added_by: string | null;
  readonly registered_at: string | null;
  readonly retired: boolean;
  readonly retired_at: string | null;
  readonly retired_by: string | null;
  readonly hub_paused: boolean;
  readonly locally_paused: boolean;
  readonly locally_paused_by: string | null;
  readonly locally_paused_reason: string | null;
  readonly agents: number | null;
  readonly env_capacity: number | null;
  readonly bindings: readonly RunnerBinding[] | null;
  readonly projects: readonly ServedProject[];
  readonly capabilities: readonly RunnerCapabilityFact[];
  readonly gates: readonly string[];
  readonly unenforceable_gates: readonly UnenforceableGate[];
  readonly declining: readonly RunnerDeclining[];
  readonly subscription: string | null;
  readonly subscription_unrostered: boolean;
  readonly subscriptions: readonly SubscriptionUsage[];
  readonly version: string | null;
  readonly version_standing: VersionStanding | null;
  readonly version_refusal: VersionRefusal | null;
}

/** A vocabulary value as rendered — a known value as itself, an unknown one as the raw word. */
export function vocabularyWord(value: string | UnknownHostValue): string {
  return typeof value === 'string' ? value : value.raw;
}

/** The projects a runner declares it serves, as the hub resolved each entry; empty when it declares none. */
export function servedProjects(runner: HubRunner): readonly ServedProject[] {
  return runner.projects;
}

/** Whether `runner` declares the project `projectId` among the projects it serves. */
export function runnerServes(runner: HubRunner, projectId: string): boolean {
  return servedProjects(runner).some((served) => served.project_id === projectId);
}

/** Whether `runner` is unassigned — it declares no project, so it is offered no work. */
export function isUnassigned(runner: HubRunner): boolean {
  return servedProjects(runner).length === 0;
}

/** The entries of `runner`'s declaration that name no project of the tenant, as the runner wrote them. */
export function unresolvedEntries(runner: HubRunner): readonly string[] {
  return servedProjects(runner)
    .filter((served) => served.project_id == null)
    .map((served) => served.entry);
}

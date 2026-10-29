// The mock fleet publishes no OpenAPI document, so the few lever and control shapes the harness uses are typed here
// by hand, and only those.
import type { Json } from './engine';
import type { RouteClaimResponse } from './wire/hub/types.gen';

/** What the mock forge answers `POST /_repos` with. */
export interface MockRepositoryCreated {
  full_name: string;
  origin: string;
  default_branch: string;
}

/** The fields of a mock-forge issue the harness reads. */
export interface MockIssue {
  number: number;
  title: string;
}

/** The mock forge's lever listing, `GET /_levers`. */
export interface MockLeverListing {
  state_levers: string[];
  action_levers: string[];
}

/** The mock runner wraps hub replies so tests can inspect both the drive and the hub outcome. */
export interface MockDriveRegister {
  status: number;
  response: { runner_id: string };
}

export interface MockDriveClaim {
  claimed: boolean;
  status?: number;
  epoch?: number;
  from_node_id?: string;
  response?: RouteClaimResponse;
}

export interface MockDriveComplete {
  drove: boolean;
  status?: number;
  response?: { outcome: string; detail?: string | null };
  /** The second submission, when the replay lever armed a duplicate delivery. */
  replayed?: { response?: { outcome: string } };
}

export interface MockDrivePeek {
  status: number;
  response: { entries: { chunk_id: string }[] };
}

/** What a drive of `report-external-usage` or `report-external-usage-miss` answers: the hub's per-report disposition. */
export interface MockDriveUsageReport {
  response: { applied: number[]; rejected: number[] };
}

export interface MockRunnerLeverListing {
  active: { kind: string; chunk_id: string | null; remaining: number | null; payload: Record<string, unknown> }[];
  levers: string[];
}

export type MockDriveAnswer<V extends string> = V extends 'register' ? MockDriveRegister : V extends 'claim' | 'claim-next' ? MockDriveClaim : V extends 'complete' ? MockDriveComplete : V extends 'peek' ? MockDrivePeek : V extends 'report-external-usage' | 'report-external-usage-miss' ? MockDriveUsageReport : Json;

/** One node of a seeded chunk's graph; only what a runner-tier test sets. */
export interface MockNodeSpec {
  executor?: string;
  session?: string;
  prompt?: string;
  judged_by?: string;
  judgement_prompt?: string;
  /** How many retries the node grants before its attempt escalates. */
  retries_max?: number;
  choices?: { name: string; to: string; description?: string }[];
  /** A hub-executed node's choices, by name. */
  judgement?: { choices: Record<string, { description: string; to: string }> };
  produces?: string[];
  /** `resume` makes the node continue a session; `session_name` pools one session across nodes. */
  session_name?: string;
  /** The harnesses the node's session may run under. */
  session_harnesses?: string[];
}

/** `POST /_seed/chunk`: a scripted chunk. Only the fields a test sets are named. */
export interface MockChunkSpec {
  chunk_id?: string;
  project_id?: string;
  graph_id?: string;
  entry: string;
  nodes: Record<string, MockNodeSpec>;
  /** The chunk's `{source, ref}` work refs. */
  work_refs?: { source: string; ref: string }[];
  /** Graph-scoped artifacts baked into the chunk's mint; `asset` is the one kind the hub synthesizes. */
  graph_artifacts?: { name: string; kind?: 'asset'; content: string }[];
  status?: 'ready' | 'not_ready' | 'paused' | 'needs_human' | 'delivering' | 'done' | 'stopped';
}

export interface MockChunkSeeded {
  chunk_id: string;
  graph_id: string;
  project_id: string;
}

export interface MockTenantSeeded {
  tenant_id: string;
  name: string;
}

export interface MockProjectSeeded {
  project_id: string;
  tenant_id: string;
  slug: string;
  name: string;
}

/** `POST /_seed/runners`: a runner added never connected, optionally under a pinned identity. */
export interface MockRunnerSeed {
  name: string;
  runner_id?: string;
  token?: string;
  tenant_id?: string;
  projects?: string[];
}

/** One lever as the mock hub lists it, `GET /_levers`. */
export interface MockHubLever {
  kind: string;
  chunk_id: string | null;
  remaining: number | null;
  payload: Record<string, unknown>;
}

export interface MockHubLeverListing {
  catalog: unknown;
  levers: string[];
  active: MockHubLever[];
}

/** One request the mock hub captured, `GET /_captured`; header names are lower-cased. */
export interface MockCapturedRequest {
  method: string;
  path: string;
  headers: Record<string, string>;
  /** The parsed JSON body; `null` when the body was empty or not JSON. */
  body: Json;
}

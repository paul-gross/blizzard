// The mock fleet publishes no OpenAPI document, so the few lever and control shapes the harness uses are typed here
// by hand, and only those.
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

export type MockDriveAnswer<V extends string> = V extends 'register' ? MockDriveRegister : V extends 'claim' | 'claim-next' ? MockDriveClaim : V extends 'complete' ? MockDriveComplete : V extends 'peek' ? MockDrivePeek : V extends 'report-external-usage' | 'report-external-usage-miss' ? MockDriveUsageReport : import('./clients').Json;

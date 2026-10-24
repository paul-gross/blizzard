// The mock fleet publishes no OpenAPI document, so the few lever and control shapes the harness uses are typed here
// by hand, and only those.

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

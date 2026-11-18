/** The link fields needed to split repositories into landing-order sections. */
export interface LandingRepository {
  readonly repository_id: string;
  readonly position: number | null;
}

/** An intent to rank a repository after an anchor, or remove it from the ranked order. */
export type LandingPosition =
  | { readonly repository: string; readonly afterRepository: string | null; readonly unranked?: never }
  | { readonly repository: string; readonly unranked: true; readonly afterRepository?: never };

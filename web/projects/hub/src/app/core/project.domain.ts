/** A project as presented by the hub board. */
export interface Project {
  readonly project_id: string;
  readonly slug: string;
  readonly name: string;
  readonly description: string;
  readonly color: string;
  readonly revision: number;
  readonly created_at: string;
}

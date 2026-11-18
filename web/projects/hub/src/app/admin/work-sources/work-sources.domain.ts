/** Catalogue facts needed to link and display a work source on a project. */
export interface ProjectWorkSource {
  readonly work_source_id: string | null;
  readonly slug: string | null;
  readonly name: string;
  readonly provider: string | null;
  readonly locator: string | null;
  readonly retired: boolean;
  readonly built_in: boolean;
}

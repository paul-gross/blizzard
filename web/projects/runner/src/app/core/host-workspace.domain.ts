/** A workspace the host declares, with its environment ceiling. */
export interface HostWorkspace {
  readonly workspace_id: string;
  readonly max_environments: number;
}

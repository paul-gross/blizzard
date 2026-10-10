import {
  getFindingApiFindingsFindingIdGet,
  getGardenProposalApiGardenProposalsProposalIdGet,
  runDeltaApiRunsChunkIdGet,
} from 'fleet';

/** The project fields a garden record's own read carries. */
export interface GardenRecordProject {
  readonly project_slug?: string | null;
  readonly project_id?: string | null;
}

/** The gardening tabs whose record segment is an id unique in the tenant, each with the read of that record. */
const RECORD_READS: Readonly<Record<string, (id: string) => Promise<GardenRecordProject | undefined>>> = {
  runs: async (id) => (await runDeltaApiRunsChunkIdGet({ path: { chunk_id: id }, throwOnError: true })).data,
  findings: async (id) => (await getFindingApiFindingsFindingIdGet({ path: { finding_id: id }, throwOnError: true })).data,
  proposals: async (id) =>
    (await getGardenProposalApiGardenProposalsProposalIdGet({ path: { proposal_id: id }, throwOnError: true })).data,
};

/** Whether the gardening tab `tab` names its records by an id unique in the tenant (a run, a finding, a proposal). */
export function isRecordTab(tab: string): boolean {
  return tab in RECORD_READS;
}

/**
 * The project of the record `id` on the gardening tab `tab` — its slug, or its id when the read carries no slug — read
 * by the record's own id (`GET /api/runs/{id}`, `/api/findings/{id}`, `/api/garden-proposals/{id}`); `null` when the
 * tenant holds no such record or its read fails.
 */
export async function readRecordProject(tab: string, id: string): Promise<string | null> {
  const read = RECORD_READS[tab];
  if (read === undefined) return null;
  const owner = await read(id).catch(() => undefined);
  return owner?.project_slug ?? owner?.project_id ?? null;
}

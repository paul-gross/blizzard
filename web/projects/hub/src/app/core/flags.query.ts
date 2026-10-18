import { type FlagsRead, flagsApiFlagsGet, hubFlagsKey } from 'fleet/shell';

/** `GET /api/flags` — the hub's own flags, read once at start and handed to the shared `FlagStore`. */
export const hubFlagsRead: FlagsRead = {
  queryKey: hubFlagsKey,
  queryFn: async () => {
    const { data, error } = await flagsApiFlagsGet({ throwOnError: false });
    if (error) throw error;
    return data!;
  },
};

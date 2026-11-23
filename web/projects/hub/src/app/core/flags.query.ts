import { type FlagsRead, flagsApiFlagsGet } from 'fleet/shell';
import { hubFlagsKey } from './query-keys';

/** `GET /api/flags` — the hub's own flags, read once at start and handed to the shared `FlagStore`. */
export const hubFlagsRead: FlagsRead = {
  queryKey: hubFlagsKey,
  queryFn: async () => {
    const { data, error } = await flagsApiFlagsGet({ throwOnError: false });
    if (error) throw error;
    return { flags: data!.flags, overridden: data!.overridden };
  },
};

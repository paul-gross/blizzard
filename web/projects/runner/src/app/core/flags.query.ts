import { type FlagsRead } from 'fleet/shell';
import { runnerApi } from 'fleet/runner-shell';
import { runnerFlagsKey } from './query-keys';

/** `GET /api/flags` on the host root — the host's own flags, read once at start and handed to the shared `FlagStore`. */
export const runnerFlagsRead: FlagsRead = {
  queryKey: runnerFlagsKey,
  queryFn: async () => {
    const { data, error } = await runnerApi.flagsApiFlagsGet({ throwOnError: false });
    if (error) throw error;
    return { flags: data!.flags, overridden: data!.overridden };
  },
};

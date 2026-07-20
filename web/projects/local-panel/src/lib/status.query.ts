import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import {
  runnerAsksKey,
  runnerEnvironmentsKey,
  runnerEscalationsKey,
  runnerFactsKey,
  runnerStatusKey,
  runnerTakeoversKey,
} from './query-keys';

/**
 * The machine-local status reads behind the panel's rails — `GET /api/runner`,
 * `/environments`, `/asks?open=true`, `/escalations`, `/takeovers`, and
 * `/facts` (the outbound-ledger fact log). All hub-free (`blizzard runner
 * status`'s own reads), all through the generated runner client
 * (`bzh:generated-client`), all on the same 5s poll floor as `leases.query.ts`
 * — the runner has no event stream, so the poll is the only signal.
 */
export function injectRunnerStatusQuery() {
  return injectQuery(() => ({
    queryKey: runnerStatusKey,
    queryFn: async (): Promise<runnerApi.RunnerStatusView> => {
      const { data, error } = await runnerApi.getRunnerApiRunnerGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
    refetchInterval: 5000,
  }));
}

export function injectRunnerEnvironmentsQuery() {
  return injectQuery(() => ({
    queryKey: runnerEnvironmentsKey,
    queryFn: async (): Promise<runnerApi.EnvironmentView[]> => {
      const { data, error } = await runnerApi.listEnvironmentsApiEnvironmentsGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: 5000,
  }));
}

export function injectRunnerAsksQuery() {
  return injectQuery(() => ({
    queryKey: runnerAsksKey,
    queryFn: async (): Promise<runnerApi.AskView[]> => {
      const { data, error } = await runnerApi.listAsksApiAsksGet({ query: { open: true }, throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: 5000,
  }));
}

export function injectRunnerEscalationsQuery() {
  return injectQuery(() => ({
    queryKey: runnerEscalationsKey,
    queryFn: async (): Promise<runnerApi.EscalationView[]> => {
      const { data, error } = await runnerApi.listEscalationsApiEscalationsGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: 5000,
  }));
}

export function injectRunnerTakeoversQuery() {
  return injectQuery(() => ({
    queryKey: runnerTakeoversKey,
    queryFn: async (): Promise<runnerApi.OpenTakeoverView[]> => {
      const { data, error } = await runnerApi.listOpenTakeoversApiTakeoversGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: 5000,
  }));
}

export function injectRunnerFactsQuery() {
  return injectQuery(() => ({
    queryKey: runnerFactsKey,
    queryFn: async (): Promise<runnerApi.FactView[]> => {
      const { data, error } = await runnerApi.listFactsApiFactsGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: 5000,
  }));
}

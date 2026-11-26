import { computed } from '@angular/core';
import { injectNowSignal } from 'fleet/core';

import { startOfLocalDayIso, startOfPreviousLocalDayIso } from '../../core/local-day';
import { injectHubFleetSpendDomainQuery } from './fleet-spend.query';

/**
 * The fleet's spend today (`[local midnight, now)`) and yesterday (`[previous local midnight, local midnight)`), cut
 * from the shared clock — the day boundaries are strings, so the query keys change only when the local day does.
 */
export function injectFleetSpendDays() {
  const now = injectNowSignal();
  const dayStart = computed(() => startOfLocalDayIso(now()));
  const previousDayStart = computed(() => startOfPreviousLocalDayIso(now()));
  return {
    today: injectHubFleetSpendDomainQuery(() => dayStart()),
    yesterday: injectHubFleetSpendDomainQuery(
      () => previousDayStart(),
      () => dayStart(),
    ),
  };
}

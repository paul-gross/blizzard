import type { ComboOption } from '../kit/kit-combobox';

/**
 * The IANA time zones this browser knows, as combobox options — `UTC` included even where the
 * browser's list omits it — sorted by name. Each option's `meta` is its UTC offset at `now`
 * (epoch ms), so a zone in daylight time reads its summer offset. Synchronous: the browser
 * owns the list, so there is no loading or failure state.
 */
export function zoneOptions(now: number): readonly ComboOption[] {
  const names = new Set<string>(Intl.supportedValuesOf('timeZone'));
  names.add('UTC');
  return [...names]
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    .map((zone) => ({ value: zone, label: zone, meta: offsetAt(zone, now) }));
}

function offsetAt(zone: string, now: number): string {
  const part = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'shortOffset' })
    .formatToParts(now)
    .find((p) => p.type === 'timeZoneName');
  return part?.value ?? '';
}

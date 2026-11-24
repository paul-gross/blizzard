import { formatAbsolute } from 'fleet';

import type { Throttle, ThrottleLever } from './throttle.domain';

const displayInstant = (instant: string): string => formatAbsolute(instant) || instant;

export interface ThrottleLine {
  readonly label: string;
  readonly text: string;
}

function leverLine(lever: ThrottleLever): ThrottleLine {
  const until = lever.until === null ? '' : ` · until ${displayInstant(lever.until)}`;
  return { label: lever.name, text: [lever.setting, lever.reading].filter(Boolean).join(' · ') + ` · ${lever.admits ? 'yes' : 'no'}${until}` };
}

/** Shared words for the machine panel and the host's read-only runner row. */
export function throttleLines(throttle: Throttle): readonly ThrottleLine[] {
  const floor = throttle.floor;
  const owner = floor.subscription ?? 'this runner';
  const hold = floor.hold;
  const floorText = floor.clear && floor.admits
    ? `open — cleared by ${floor.clear.by} at ${displayInstant(floor.clear.cleared_at)}`
    : hold
      ? `exhausted — ${hold.kind}${hold.harness_id ? ` via ${hold.harness_id}` : ''}, resets ${displayInstant(hold.expected_reset)}${hold.awaits_sample ? ` · awaiting fresh sample${hold.grace_opens_at ? `, opens ${displayInstant(hold.grace_opens_at)}` : ''}` : ''}`
      : 'open';
  return [
    { label: 'Floor', text: `${owner}: ${floorText} · ${floor.admits ? 'yes' : 'no'}` },
    ...throttle.levers.map(leverLine),
    { label: 'On decline / usage limit', text: `${throttle.on_decline} / ${throttle.on_usage_limit}` },
  ];
}

export function throttleSummary(throttle: Throttle): string {
  const first = throttle.declining[0];
  const lever = throttle.levers.find((l) => l.name === first && !l.admits);
  const until = lever?.until ?? null;
  const reset = first === 'floor' ? throttle.floor.hold?.expected_reset : until;
  return `${throttle.state}${first ? ` — ${first}${reset ? `, ${first === 'floor' ? 'resets' : 'until'} ${displayInstant(reset)}` : ''}` : ''}`;
}

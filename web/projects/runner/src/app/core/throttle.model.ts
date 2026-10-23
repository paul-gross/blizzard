import type { runnerApi } from 'fleet';

export interface ThrottleLine {
  readonly label: string;
  readonly text: string;
}

function leverLine(lever: runnerApi.ThrottleStatusView['levers'][number]): ThrottleLine {
  const name = typeof lever['name'] === 'string' ? lever['name'] : 'Lever';
  const setting = typeof lever['setting'] === 'string' ? lever['setting'] : '';
  const reading = typeof lever['reading'] === 'string' ? lever['reading'] : '';
  const until = typeof lever['until'] === 'string' ? ` · until ${lever['until']}` : '';
  const admits = lever['admits'] === true ? 'yes' : lever['admits'] === false ? 'no' : 'unknown';
  return { label: name, text: [setting, reading].filter(Boolean).join(' · ') + ` · ${admits}${until}` };
}

/** Shared words for the machine panel and the host's read-only runner row. */
export function throttleLines(throttle: runnerApi.ThrottleStatusView): readonly ThrottleLine[] {
  const floor = throttle.floor;
  const owner = floor.subscription ?? 'this runner';
  const hold = floor.hold;
  const floorText = floor.clear && floor.admits
    ? `open — cleared by ${floor.clear.by} at ${floor.clear.cleared_at}`
    : hold
      ? `exhausted — ${hold.kind}${hold.harness_id ? ` via ${hold.harness_id}` : ''}, resets ${hold.expected_reset}${hold.awaits_sample ? ` · awaiting fresh sample${hold.grace_opens_at ? `, opens ${hold.grace_opens_at}` : ''}` : ''}`
      : 'open';
  return [
    { label: 'Floor', text: `${owner}: ${floorText} · ${floor.admits ? 'yes' : 'no'}` },
    ...throttle.levers.map(leverLine),
    { label: 'On decline / usage limit', text: `${throttle.on_decline} / ${throttle.on_usage_limit}` },
  ];
}

export function throttleSummary(throttle: runnerApi.ThrottleStatusView): string {
  const first = throttle.declining[0];
  const reset = first === 'floor' ? throttle.floor.hold?.expected_reset : null;
  return `${throttle.state}${first ? ` — ${first}${reset ? `, resets ${reset}` : ''}` : ''}`;
}

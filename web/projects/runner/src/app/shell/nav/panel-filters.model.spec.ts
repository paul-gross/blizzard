import type { runnerApi } from 'fleet';
import { describe, expect, it } from 'vitest';

import type { PanelProject } from '../../core/panel-reads.model';
import { brakeLabel, projectFilterChoices, runnerFilterChoices } from './panel-filters.model';

function runner(name: string, overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView {
  return {
    runner_id: `rn_${name}`,
    name,
    state: 'serving',
    pause: { local: false, hub: false, effective: false, local_reason: null },
    ...overrides,
  };
}

describe('runnerFilterChoices', () => {
  it('offers All first, then every runner in wire order, by name', () => {
    const choices = runnerFilterChoices([runner('r-claude'), runner('r-chatgpt')]);

    expect(choices.map((choice) => [choice.value, choice.label])).toEqual([
      [null, 'All'],
      ['r-claude', 'r-claude'],
      ['r-chatgpt', 'r-chatgpt'],
    ]);
    expect(choices[0]).toMatchObject({ title: 'every runner', disabled: false, brake: null });
  });

  it('offers an unbraked serving runner plainly, titled by its name', () => {
    expect(runnerFilterChoices([runner('r1')])[1]).toEqual({
      value: 'r1',
      label: 'r1',
      title: 'r1',
      disabled: false,
      brake: null,
      color: null,
    });
  });

  it('marks a runner braked here with the pause glyph, its reason only in the title', () => {
    const choice = runnerFilterChoices([
      runner('r1', { pause: { local: true, hub: false, effective: true, local_reason: 'usage limit' } }),
    ])[1];

    expect(choice.brake).toBe('pause');
    expect(choice.label).toBe('r1');
    expect(choice.title).toBe('r1 · paused: usage limit');
    expect(choice.disabled).toBe(false);
  });

  it('marks a runner braked by the hub with the stop glyph, as it does one braked by both', () => {
    const [, byHub, byBoth] = runnerFilterChoices([
      runner('r1', { pause: { local: false, hub: true, effective: true, local_reason: null } }),
      runner('r2', { pause: { local: true, hub: true, effective: true, local_reason: null } }),
    ]);

    expect(byHub.brake).toBe('stop');
    expect(byHub.title).toBe('r1 · paused by hub');
    expect(byBoth.brake).toBe('stop');
    expect(byBoth.title).toBe('r2 · paused here and by hub');
  });

  it('offers a halted runner disabled, with no brake glyph', () => {
    const choice = runnerFilterChoices([
      runner('r-broken', {
        runner_id: null,
        state: 'halted',
        pause: { local: true, hub: false, effective: true, local_reason: null },
      }),
    ])[1];

    expect(choice).toEqual({
      value: 'r-broken',
      label: 'r-broken',
      title: 'r-broken is halted',
      disabled: true,
      brake: null,
      color: null,
    });
  });
});

describe('runnerFilterChoices with a stale current filter', () => {
  it('offers a filter naming no runner on the host last, disabled and titled as gone', () => {
    const choices = runnerFilterChoices([runner('r1')], 'r-removed');

    expect(choices.map((choice) => choice.value)).toEqual([null, 'r1', 'r-removed']);
    expect(choices[2]).toEqual({
      value: 'r-removed',
      label: 'r-removed (gone)',
      title: 'r-removed is no longer on this host',
      disabled: true,
      brake: null,
      color: null,
    });
  });

  it('adds nothing when the current filter names a runner on the host, or is All', () => {
    expect(runnerFilterChoices([runner('r1')], 'r1')).toHaveLength(2);
    expect(runnerFilterChoices([runner('r1')], null)).toHaveLength(2);
  });

  it('calls nothing gone while the runner list is still pending', () => {
    expect(runnerFilterChoices([], 'r1', true)).toHaveLength(1);
  });

  it('names a halted runner still on the host as present, not gone', () => {
    expect(runnerFilterChoices([runner('r1', { runner_id: null, state: 'halted' })], 'r1')).toHaveLength(2);
  });
});

describe('projectFilterChoices', () => {
  it('offers All first, then every project by name, valued by its slug and carrying its colour', () => {
    const projects: PanelProject[] = [
      { slug: 'blizzard', name: 'Blizzard', entries: ['ws'], color: 'blue' },
      { slug: 'winter', name: 'Winter', entries: [], color: null },
    ];

    expect(projectFilterChoices(projects)).toEqual([
      { value: null, label: 'All', title: 'every project', disabled: false, brake: null, color: null },
      { value: 'blizzard', label: 'Blizzard', title: 'blizzard', disabled: false, brake: null, color: 'blue' },
      { value: 'winter', label: 'Winter', title: 'winter', disabled: false, brake: null, color: null },
    ]);
  });
});

describe('brakeLabel', () => {
  it('reads null while neither brake is set', () => {
    expect(brakeLabel(null)).toBeNull();
    expect(brakeLabel({ local: false, hub: false, effective: false })).toBeNull();
  });

  it('names which side set the brake', () => {
    expect(brakeLabel({ local: true, hub: false, effective: true })).toBe('paused');
    expect(brakeLabel({ local: false, hub: true, effective: true })).toBe('paused by hub');
    expect(brakeLabel({ local: true, hub: true, effective: true })).toBe('paused here and by hub');
  });

  it('carries the local brake reason', () => {
    expect(brakeLabel({ local: true, hub: false, effective: true, local_reason: 'usage limit' })).toBe('paused: usage limit');
  });
});

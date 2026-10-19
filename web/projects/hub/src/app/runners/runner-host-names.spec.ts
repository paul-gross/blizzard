import { describe, expect, it } from 'vitest';

import { hostNamesByRunner } from './runner-host-names';

describe('hostNamesByRunner', () => {
  it("maps every runner a host lists to that host's name", () => {
    const hosts = hostNamesByRunner([
      { host_name: 'atlas', runner_ids: ['rn_1', 'rn_2'] },
      { host_name: 'xps', runner_ids: ['rn_3'] },
    ]);
    expect([...hosts]).toEqual([
      ['rn_1', 'atlas'],
      ['rn_2', 'atlas'],
      ['rn_3', 'xps'],
    ]);
  });

  it('maps nothing for a host listing no runners, or no hosts at all', () => {
    expect(hostNamesByRunner([{ host_name: 'atlas' }, { host_name: 'xps', runner_ids: [] }]).size).toBe(0);
    expect(hostNamesByRunner([]).size).toBe(0);
  });
});

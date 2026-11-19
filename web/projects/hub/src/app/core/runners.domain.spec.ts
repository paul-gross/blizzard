import { fleetIdOf } from './runners.domain';

describe('fleetIdOf', () => {
  const hosts = [{ host_id: 'hs_1', runner_ids: ['rn_1', 'rn_2'] }, { host_id: 'hs_2' }];

  it('names the host that lists the runner', () => {
    expect(fleetIdOf('rn_2', hosts)).toBe('hs_1');
  });

  it('names a runner no host lists, or any runner when there are no hosts, by itself', () => {
    expect(fleetIdOf('rn_9', hosts)).toBe('rn_9');
    expect(fleetIdOf('rn_1', [])).toBe('rn_1');
  });
});

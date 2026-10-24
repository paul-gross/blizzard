import { pauseCopy } from './chunk-action-copy';

describe('pauseCopy', () => {
  it('names the claimant and parks the agent when a runner holds the chunk', () => {
    const copy = pauseCopy('xps');
    expect(copy.subtitle).toBe('Park the agent, keep the claim');
    expect(copy.text).toContain('xps keeps the claim');
  });

  it('promises no worker or session for an unclaimed chunk', () => {
    const copy = pauseCopy(null);
    expect(copy.subtitle).toBe('Hold it out of the queue');
    expect(copy.text).toContain('No runner has claimed it yet');
    expect(copy.text).not.toMatch(/session|worker/);
  });
});

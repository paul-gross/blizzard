import { stubHubClient, type RequestClientStub } from 'fleet/testing';
import { readUrgencies, urgenciesQueryOptions } from './urgencies.query';
import { readUrgencyIcon } from './urgency-icon.query';

describe('urgency reads', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());
  it('requests retired records and configures observed refresh', async () => {
    stub = stubHubClient({ 'GET /api/urgencies': () => ({ urgencies: [] }) });
    await expect(readUrgencies()).resolves.toEqual([]);
    expect(new URLSearchParams(stub.requests[0].search).get('retired')).toBe('true');
    expect(urgenciesQueryOptions()).toMatchObject({ refetchInterval: 15000, refetchOnWindowFocus: true, refetchOnReconnect: true });
  });
  it('rejects non-image responses from the icon operation', async () => {
    stub = stubHubClient({ 'GET /api/urgencies/{key}/icon': () => ({ invalid: true }) });
    await expect(readUrgencyIcon('u')).rejects.toThrow('Invalid urgency icon');
  });
});

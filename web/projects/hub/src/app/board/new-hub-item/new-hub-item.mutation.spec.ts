import { stubHubClient, type RequestClientStub } from 'fleet/testing';
import { createHubItem } from './new-hub-item.mutations';
import { CREATED_ITEM } from './new-hub-item.test-data';

describe('createHubItem', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());
  it('creates on the hub source with project and urgency and returns the minted chunk', async () => {
    stub = stubHubClient({ 'POST /api/work-sources/{source}/items': () => CREATED_ITEM });
    await expect(createHubItem('project-id', 'Title', 'Body', 'urgent-id')).resolves.toBe('ch_created');
    expect(stub.requests[0].body).toEqual({ title: 'Title', body: 'Body', urgency: 'urgent-id' });
    expect(stub.requests[0].path).toContain('/api/work-sources/hub/items');
    expect(new URLSearchParams(stub.requests[0].search).get('project')).toBe('project-id');
  });
});

import { client as hubClient } from '../api/hub/client.gen';
import { client as runnerClient } from '../api/runner/client.gen';
import { stubError, stubHubClient, stubRunnerClient, type RequestClientStub } from './stub-request-client';

const PROJECT = {
  color: 'blue',
  created_at: '2026-01-01T00:00:00Z',
  description: '',
  name: 'Alpha',
  project_id: 'pr_alpha',
  revision: 1,
  slug: 'alpha',
};

describe('stubHubClient', () => {
  let stub: RequestClientStub | undefined;
  afterEach(() => stub?.restore());

  it('matches a templated route to its concrete path and hands the handler the resolved params', async () => {
    stub = stubHubClient({
      'GET /api/projects/{project}': (_request, params) => ({ ...PROJECT, slug: params.project }),
    });

    const { data } = await hubClient.get({ url: '/api/projects/beta' });

    expect(data).toEqual({ ...PROJECT, slug: 'beta' });
  });

  it('prefers the route with more literal segments when two templates match', async () => {
    stub = stubHubClient({
      'GET /api/routines/{routine_id}': () => stubError(409, { detail: 'param' }),
      'GET /api/routines/trend': () => stubError(409, { detail: 'literal' }),
    });

    const trend = await hubClient.get({ url: '/api/routines/trend' });
    const one = await hubClient.get({ url: '/api/routines/rt_1' });

    expect(trend.error).toEqual({ detail: 'literal' });
    expect(one.error).toEqual({ detail: 'param' });
  });

  it('answers a request that matches no declared route with a 404 naming it', async () => {
    stub = stubHubClient({});

    const { response, error } = await hubClient.get({ url: '/api/projects/alpha' });

    expect(response?.status).toBe(404);
    expect(error).toEqual({ detail: 'No stub route for GET /api/projects/alpha' });
  });

  it('does not let a `{param}` match an empty segment or a deeper path', async () => {
    stub = stubHubClient({ 'GET /api/projects/{project}': () => PROJECT });

    const deeper = await hubClient.get({ url: '/api/projects/alpha/scopes' });

    expect(deeper.response?.status).toBe(404);
  });

  it('keeps capture unchanged: method, path, body, search, headers, forRoute', async () => {
    stub = stubHubClient({ 'PATCH /api/projects/{project}': () => PROJECT });

    await hubClient.patch({ url: '/api/projects/alpha?x=1', body: { a: 1 }, headers: { 'X-Probe': 'yes' } });

    const [request] = stub.forRoute('/api/projects/alpha', 'patch');
    expect(stub.requests).toHaveLength(1);
    expect(request).toMatchObject({ method: 'PATCH', path: '/api/projects/alpha', body: { a: 1 }, search: '?x=1' });
    expect(request.headers['x-probe']).toBe('yes');
  });

  it('answers a declared error status with its declared body', async () => {
    stub = stubHubClient({
      'POST /api/decisions/{decision_id}/resolutions': () =>
        stubError(409, {
          already_resolved_by: 'us_1',
          decision_id: 'dc_1',
          detail: 'lost the race',
          resolved_at: '2026-01-01T00:00:00Z',
        }),
    });

    const { response } = await hubClient.post({ url: '/api/decisions/dc_1/resolutions', body: {} });

    expect(response?.status).toBe(409);
  });

  it('refuses, at compile time, a canned body that is incomplete, mistyped, or untyped', () => {
    stub = stubHubClient({
      // @ts-expect-error — a body missing required fields does not compile
      'GET /api/projects/{project}': () => ({ slug: 'alpha' }),
    });
    stub.restore();
    stub = stubHubClient({
      // @ts-expect-error — a field of the wrong type does not compile
      'GET /api/projects/{project}': () => ({ ...PROJECT, revision: 'one' }),
    });
    stub.restore();
    stub = stubHubClient({
      // @ts-expect-error — a handler returning `unknown` does not compile
      'GET /api/projects/{project}': (): unknown => PROJECT,
    });
    stub.restore();
    stub = stubHubClient({
      // @ts-expect-error — a wrong body for a declared error status does not compile
      'POST /api/decisions/{decision_id}/resolutions': () => stubError(409, { unrelated: true }),
    });
    stub.restore();
    stub = stubHubClient({
      // @ts-expect-error — a key naming no hub operation does not compile
      'GET /api/not-an-operation': () => ({}),
    });
    stub.restore();
    stub = undefined;
    expect(true).toBe(true);
  });
});

describe('stubRunnerClient', () => {
  let stub: RequestClientStub | undefined;
  afterEach(() => stub?.restore());

  it('binds the runner client and matches its prefixed templates', async () => {
    stub = stubRunnerClient({});

    const { response } = await runnerClient.get({ url: '/api/runners/rn_1/anything' });

    expect(response?.status).toBe(404);
    expect(stub.requests).toHaveLength(1);
  });
});

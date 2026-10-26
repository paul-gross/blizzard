import { type CallRecorder, timed } from './latency';
import type { AdminGraphSyncResponse, ApplyResponse, TenantView } from './wire/hub/types.gen';

/**
 * The internal client: hub-level calls the harness's own fixtures make (tenant creation, furnishing), which no spec can
 * reach. On a hub with authentication off the harness acts as the implicit operator; on one with it on, the client
 * carries the superuser's `bz_session`. Either way it names its tenant in `X-Blizzard-Tenant`. Given a recorder, it
 * leaves a latency record for each call, tenant creation among them.
 */
export class HubInternal {
  constructor(
    readonly baseUrl: string,
    private readonly record?: CallRecorder,
    /** The superuser's `bz_session` value, on a hub with authentication on. */
    private readonly session?: string,
  ) {}

  async call<T>(method: string, route: string, options: { tenant?: string; body?: unknown } = {}): Promise<T> {
    const headers: Record<string, string> = {};
    if (options.tenant !== undefined) headers['X-Blizzard-Tenant'] = options.tenant;
    if (this.session !== undefined) headers.cookie = `bz_session=${this.session}`;
    if (options.body !== undefined) headers['content-type'] = 'application/json';
    const send = (): Promise<Response> =>
      fetch(`${this.baseUrl}${route}`, {
        method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: AbortSignal.timeout(30_000),
      });
    const response = this.record === undefined ? await send() : await timed(this.record, method, route.split('?')[0] as string, send, (answer) => answer.status);
    const text = await response.text();
    if (!response.ok) throw new Error(`${method} ${route} answered ${response.status}: ${text}`);
    return (text === '' ? undefined : JSON.parse(text)) as T;
  }

  createTenant(name: string, adminUserId?: string): Promise<TenantView> {
    return this.call<TenantView>('POST', '/api/admin/tenants', { body: { name, ...(adminUserId === undefined ? {} : { admin_user_id: adminUserId }) } });
  }

  listTenants(): Promise<TenantView[]> {
    return this.call<TenantView[]>('GET', '/api/admin/tenants');
  }

  /** `graph sync`: the packaged graphs reconciled into every open tenant, as a deployment runs it after each deploy. */
  syncGraphs(): Promise<AdminGraphSyncResponse> {
    return this.call<AdminGraphSyncResponse>('POST', '/api/admin/graphs/sync');
  }

  /** A raw answer, for the checks that assert on a refusal rather than a success. */
  async probe(method: string, route: string, headers: Record<string, string> = {}): Promise<{ status: number; body: unknown }> {
    if (this.session !== undefined) headers = { ...headers, cookie: `bz_session=${this.session}` };
    const response = await fetch(`${this.baseUrl}${route}`, { method, headers });
    const text = await response.text();
    let body: unknown = text;
    try {
      body = JSON.parse(text);
    } catch {
      // Not JSON: the text stands.
    }
    return { status: response.status, body };
  }

  applyConfig(tenant: string, document: unknown): Promise<ApplyResponse> {
    return this.call<ApplyResponse>('POST', '/api/config/apply', { tenant, body: document });
  }
}

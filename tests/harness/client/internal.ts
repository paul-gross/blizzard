import { type CallRecorder, timed } from './latency';
import type { ApplyResponse, TenantView } from './wire/hub/types.gen';

/**
 * The internal client: hub-level calls the harness's own fixtures make (tenant creation, furnishing), which no spec can
 * reach. Given a recorder, it leaves a latency record for each call, tenant creation among them. The shared hubs run with authentication off, so the harness acts as the implicit operator and names its tenant
 * in `X-Blizzard-Tenant`.
 */
export class HubInternal {
  constructor(
    readonly baseUrl: string,
    private readonly record?: CallRecorder,
  ) {}

  async call<T>(method: string, route: string, options: { tenant?: string; body?: unknown } = {}): Promise<T> {
    const headers: Record<string, string> = {};
    if (options.tenant !== undefined) headers['X-Blizzard-Tenant'] = options.tenant;
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

  createTenant(name: string): Promise<TenantView> {
    return this.call<TenantView>('POST', '/api/admin/tenants', { body: { name } });
  }

  listTenants(): Promise<TenantView[]> {
    return this.call<TenantView[]>('GET', '/api/admin/tenants');
  }

  /** A raw answer, for the checks that assert on a refusal rather than a success. */
  async probe(method: string, route: string, headers: Record<string, string> = {}): Promise<{ status: number; body: unknown }> {
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

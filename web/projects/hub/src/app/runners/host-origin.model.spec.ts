import type { FleetSelection } from './fleet-page.model';
import { selectHostOrigin } from './host-origin.model';

const host = (urls: string[], url: string | null = null): FleetSelection =>
  ({ kind: 'host', card: { urls, url } }) as unknown as FleetSelection;

describe('selectHostOrigin', () => {
  it('selects the first valid HTTP(S) URL on the browser hostname, ignoring ports', () => {
    expect(selectHostOrigin(host(['bad', 'ftp://localhost:80', 'http://other', 'https://LOCALHOST:42/a', 'http://localhost:3']), 'localhost'))
      .toEqual({ kind: 'link', href: 'https://LOCALHOST:42/a' });
  });
  it('uses the canonical URL only when the candidate list is empty', () => {
    expect(selectHostOrigin(host([], 'http://localhost:42'), 'localhost')).toEqual({ kind: 'link', href: 'http://localhost:42' });
    expect(selectHostOrigin(host(['http://other'], 'http://localhost:42'), 'localhost')).toEqual({ kind: 'none' });
  });
  it('does not treat loopback aliases as equal or link without a browser hostname', () => {
    expect(selectHostOrigin(host(['http://127.0.0.1']), 'localhost')).toEqual({ kind: 'none' });
    expect(selectHostOrigin(host(['http://localhost']), undefined)).toEqual({ kind: 'none' });
    expect(selectHostOrigin(null, 'localhost')).toEqual({ kind: 'none' });
  });
});

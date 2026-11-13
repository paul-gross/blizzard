import { type OriginProbe, selectOrigin } from './host-origin.model';

const A = 'https://a.example';
const B = 'https://b.example';
const host = (origin: string, hostId: string | null): OriginProbe => ({ origin, kind: 'host', hostId });
const down = (origin: string): OriginProbe => ({ origin, kind: 'unreachable' });

describe('selectOrigin', () => {
  it('links the first origin when it is verified', () => {
    expect(selectOrigin([host(A, 'hs_1'), host(B, 'hs_1')], 'hs_1', A)).toEqual({ kind: 'verified', href: A });
  });
  it('links the second when the first is unreachable', () => {
    expect(selectOrigin([down(A), host(B, 'hs_1')], 'hs_1', A)).toEqual({ kind: 'verified', href: B });
  });
  it('skips an origin answering as another host', () => {
    expect(selectOrigin([host(A, 'hs_other'), host(B, 'hs_1')], 'hs_1', A)).toEqual({ kind: 'verified', href: B });
  });
  it('skips an origin whose host is not yet recorded', () => {
    expect(selectOrigin([host(A, null)], 'hs_1', A)).toEqual({ kind: 'unreachable' });
  });
  it('is unreachable when none answers', () => {
    expect(selectOrigin([down(A), down(B)], 'hs_1', A)).toEqual({ kind: 'unreachable' });
  });
  it('falls back to the plain url for a legacy answer', () => {
    expect(selectOrigin([down(A), { origin: B, kind: 'legacy' }], 'hs_1', A)).toEqual({ kind: 'plain', href: A });
  });
  it('keeps the no-url rendering with no origins', () => {
    expect(selectOrigin([], 'hs_1', null)).toEqual({ kind: 'none' });
  });
});

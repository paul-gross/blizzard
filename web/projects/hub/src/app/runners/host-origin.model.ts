import type { FleetSelection } from './fleet-page.model';

/** A matching reported host URL, or no navigation link. */
export type HostOrigin = { readonly kind: 'link'; readonly href: string } | { readonly kind: 'none' };

/** Select the first reported HTTP(S) URL on the browser's hostname, without contacting it. */
export function selectHostOrigin(selection: FleetSelection | null, hostname: string | undefined): HostOrigin {
  if (selection?.kind !== 'host' || !hostname) return { kind: 'none' };
  const { urls, url } = selection.card;
  const candidates = urls.length > 0 ? urls : url === null ? [] : [url];
  for (const candidate of candidates) {
    try {
      const parsed = new URL(candidate);
      if ((parsed.protocol === 'http:' || parsed.protocol === 'https:') && parsed.hostname === hostname) {
        return { kind: 'link', href: candidate };
      }
    } catch {
      // A malformed reported URL is not a navigation candidate.
    }
  }
  return { kind: 'none' };
}

import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { chunkDetail, prView } from '../../testing/builders/chunk';
import { ChunkDelivery } from './chunk-delivery';

describe('ChunkDelivery', () => {
  it('shows a parked merge wait, its start and PR link', async () => {
    const fixture = TestBed.createComponent(ChunkDelivery);
    const pr = prView();
    fixture.componentRef.setInput('detail', chunkDetail({
      pending: { node_name: 'park', next_poll_at: '2026-10-30T12:05:00Z', kind: 'awaiting-merge', since: '2026-10-30T12:00:00Z' },
      open_prs: [pr],
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="delivery-park-wait"]')?.textContent).toContain('Waiting since');
    expect(el.querySelector('fleet-when')?.getAttribute('title')).toBeTruthy();
    expect(el.querySelector<HTMLAnchorElement>('[data-testid="delivery-pr-link"]')?.href).toBe(pr.url);
    expect(el.querySelector('[data-testid="delivery-merge-wait"]')).toBeNull();
  });

  it('keeps other pending kinds distinct and tolerates an absent wait start', async () => {
    const fixture = TestBed.createComponent(ChunkDelivery);
    fixture.componentRef.setInput('detail', chunkDetail({
      awaiting_external_merge: true,
      pending: { node_name: 'land', next_poll_at: '2026-10-30T12:05:00Z', kind: 'future-kind' },
    }));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="delivery-park-wait"]')).toBeNull();
    expect(el.querySelector('[data-testid="delivery-merge-wait"]')).not.toBeNull();
    fixture.componentRef.setInput('detail', chunkDetail({
      pending: { node_name: 'park', next_poll_at: '2026-10-30T12:05:00Z', kind: 'awaiting-merge', since: null },
    }));
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="delivery-park-wait"]')?.textContent).not.toContain('Waiting since');
  });
});

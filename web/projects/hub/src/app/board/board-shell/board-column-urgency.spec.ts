import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { UrgencyView } from 'fleet';
import type { BoardCard } from '../board-card/board-card-view.model';
import { BoardColumn } from './board-column';

const card = { chunkId: 'ch_a', shortId: 'C-A', status: 'ready', terminal: false, promotable: false, node: 'build', nodeId: '', graphName: null, graphProgress: [], workLines: [{ label: 'hub:1', title: 'Title' }], itemsRead: true, costUsd: 0, costPartial: false, estimatedCostUsd: null, completedAt: null, blockedOn: null, blockedCount: 0, blockedOnStatus: null, urgency: { urgency_id: 'u', slug: 'urgent' } } as BoardCard;
const urgent = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false, emoji: '🔥', icon: null, color: 'red' } as UrgencyView;

describe('BoardColumn urgency', () => {
  it.each([false, true])('projects the banner above work in both drag modes (drag=%s)', async (drag) => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(BoardColumn);
    fixture.componentRef.setInput('column', { key: 'ready', label: 'READY', headerLabel: 'Ready' });
    fixture.componentRef.setInput('cards', [card]);
    fixture.componentRef.setInput('urgencies', [urgent]);
    fixture.componentRef.setInput('reorderControls', drag);
    fixture.componentRef.setInput('canReorder', drag);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const pill = root.querySelector('fleet-chunk-urgency');
    const work = root.querySelector('[data-testid="work-ref-chip"]');
    expect(pill).not.toBeNull();
    expect(pill!.compareDocumentPosition(work!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fixture.componentRef.setInput('cards', [{ ...card, terminal: true }]);
    await fixture.whenStable();
    expect(root.querySelector('fleet-chunk-urgency')).toBeNull();
    fixture.componentRef.setInput('cards', [card]);
    fixture.componentRef.setInput('urgencies', [{ ...urgent, default: true }]);
    await fixture.whenStable();
    expect(root.querySelector('fleet-chunk-urgency')).toBeNull();
  });
  it("shows the image mark of the card's (pending) urgency from the icon URL map", async () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const imaged = { ...urgent, urgency_id: 'p', slug: 'pending', name: 'Pending', emoji: null, icon: { digest: 'd', media_type: 'image/png' } } as UrgencyView;
    const fixture = TestBed.createComponent(BoardColumn);
    fixture.componentRef.setInput('column', { key: 'ready', label: 'READY', headerLabel: 'Ready' });
    fixture.componentRef.setInput('cards', [{ ...card, urgency: { urgency_id: 'p', slug: 'pending' } }]);
    fixture.componentRef.setInput('urgencies', [urgent, imaged]);
    fixture.componentRef.setInput('urgencyIconUrls', { p: 'blob:pending', u: 'blob:urgent' });
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('fleet-chunk-urgency img.mark')?.getAttribute('src')).toBe('blob:pending');
  });
});

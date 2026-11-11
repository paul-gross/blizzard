import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { UrgencyView } from 'fleet';
import { TenantContext } from '../../core/auth/tenant-context';
import type { BoardCard } from '../board-card/board-card-view.model';
import { BoardColumn } from './board-column';
import { chunkControlStub } from 'fleet/testing';
import { vi } from 'vitest';

const card = { chunkId: 'ch_a', shortId: 'C-A', status: 'ready', terminal: false, promotable: false, node: 'build', nodeId: '', graphName: null, graphProgress: [], workLines: [{ label: 'hub:1', title: 'Title' }], itemsRead: true, costUsd: 0, costPartial: false, estimatedCostUsd: null, completedAt: null, blockedOn: null, blockedCount: 0, blockedOnStatus: null, urgency: { urgency_id: 'u', slug: 'urgent' } } as BoardCard;
const urgent = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false, emoji: '🔥', icon: null, color: 'red' } as UrgencyView;

describe('BoardColumn urgency', () => {
  it.each([false, true])('opens the card urgency menu and writes the selection (drag=%s)', async (drag) => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient()), { provide: TenantContext, useValue: { current: signal(null) } }] });
    const normal = { ...urgent, urgency_id: 'normal', slug: 'normal', name: 'Normal', default: true };
    const set = vi.fn();
    const control = { ...chunkControlStub(), urgency: { values: signal([normal, urgent]), pending: () => null, set } };
    const fixture = TestBed.createComponent(BoardColumn);
    fixture.componentRef.setInput('column', { key: 'ready', label: 'READY', headerLabel: 'Ready' });
    fixture.componentRef.setInput('cards', [card]);
    fixture.componentRef.setInput('chunkControl', control);
    fixture.componentRef.setInput('reorderControls', drag);
    fixture.componentRef.setInput('canReorder', drag);
    const select = vi.fn();
    fixture.componentInstance.selectChunk.subscribe(select);
    await fixture.whenStable();
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="chunk-urgency-menu"]')!.click();
    await fixture.whenStable();
    const options = [...document.querySelectorAll<HTMLElement>('[data-testid="chunk-urgency-options"] [role="menuitemradio"]')];
    options.find((option) => option.textContent?.includes('Normal'))!.click();
    expect(set).toHaveBeenCalledWith('ch_a', 'normal');
    expect(select).not.toHaveBeenCalled();
    fixture.destroy();
  });
  it.each([false, true])('projects the pill above work in both drag modes (drag=%s)', async (drag) => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient()), { provide: TenantContext, useValue: { current: signal(null) } }] });
    const fixture = TestBed.createComponent(BoardColumn);
    fixture.componentRef.setInput('column', { key: 'ready', label: 'READY', headerLabel: 'Ready' });
    fixture.componentRef.setInput('cards', [card]);
    fixture.componentRef.setInput('urgencies', [urgent]);
    fixture.componentRef.setInput('reorderControls', drag);
    fixture.componentRef.setInput('canReorder', drag);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const pill = root.querySelector('app-urgency-pill');
    const work = root.querySelector('[data-testid="work-ref-chip"]');
    expect(pill).not.toBeNull();
    expect(pill!.compareDocumentPosition(work!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fixture.componentRef.setInput('cards', [{ ...card, terminal: true }]);
    await fixture.whenStable();
    expect(root.querySelector('app-urgency-pill')).toBeNull();
  });
});

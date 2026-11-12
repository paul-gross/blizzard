import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { bounceView, chunkDetail, chunkUsageView, transitionView } from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';

describe('ChunkTimeline graph runs', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  const at = (index: number) => `2026-07-13T00:00:${String(index + 1).padStart(2, '0')}Z`;

  async function render(graphs: readonly (string | null)[], active: string | null = null) {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('heading', false);
    fixture.componentRef.setInput('graphLinkBase', ['/graphs']);
    fixture.componentRef.setInput('detail', chunkDetail({
      graph_id: active ?? 'gr_current', graph_name: 'current',
      status: active ? 'running' : 'done', current_node_id: 'nd_active', latest_epoch: graphs.length + 1,
      history: graphs.flatMap((graph, index) => graph === null ? [] : [transitionView({
        graph_id: graph, graph_name: 'same-name', from_node_id: `nd_${index}`, from_node_name: `node-${index}`,
        epoch: index + 1, recorded_at: at(index),
      })]),
      bounces: graphs.flatMap((graph, index) => graph === null ? [bounceView({
        envelope: `diagnostic-${index}`, recorded_at: at(index),
      })] : []),
    }));
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it.each([
    { graphs: [null, 'gr_1', 'gr_1'], headers: ['gr_1'] },
    { graphs: [null, null], headers: [] },
    { graphs: ['gr_1', null, 'gr_1', null], headers: ['gr_1'] },
    { graphs: ['gr_v1', null, 'gr_v2', null, 'gr_v1'], headers: ['gr_v1', 'gr_v2', 'gr_v1'] },
  ])('ignores graphless events for headers, without reordering $graphs', async ({ graphs, headers }) => {
    const el = await render(graphs);
    const links = [...el.querySelectorAll<HTMLAnchorElement>('[data-testid="history-graph"]')];
    expect(links.map((link) => link.getAttribute('href'))).toEqual(headers.map((id) => `/graphs/${id}`));
    expect(links.map((link) => link.textContent?.trim())).toEqual(headers.map(() => 'same-name'));
    const rows = [...el.querySelectorAll('.step-item > .step')];
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual(graphs.map((graph) => graph ? 'history-step' : 'history-bounce-step'));
    expect(rows.map((row, index) => row.textContent?.includes(graphs[index] ? `node-${index}` : `diagnostic-${index}`))).toEqual(graphs.map(() => true));
    expect(el.querySelector('.step [data-testid="history-graph"]')).toBeNull();
  });

  it.each([
    { graphs: ['gr_1', null], active: 'gr_1', headers: ['gr_1'] },
    { graphs: ['gr_1', null], active: 'gr_2', headers: ['gr_1', 'gr_2'] },
    { graphs: [null, null], active: 'gr_2', headers: ['gr_2'] },
    { graphs: [], active: 'gr_2', headers: ['gr_2'] },
  ])('compares the active graph to the last known graph: $graphs → $active', async ({ graphs, active, headers }) => {
    const el = await render(graphs, active);
    expect([...el.querySelectorAll('[data-testid="history-graph"]')].map((link) => link.getAttribute('href'))).toEqual(headers.map((id) => `/graphs/${id}`));
    expect(el.querySelector('[data-testid="history-active"]')).not.toBeNull();
  });

  it('opts into dollars-only usage without changing the rich default or step selection', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', chunkDetail({
      status: 'done', history: [transitionView({ from_node_id: 'nd_build', epoch: 1 })],
      usage: [chunkUsageView({ node_id: 'nd_build', epoch: 1, cost_usd: 0.42 })],
    }));
    fixture.componentRef.setInput('activatable', true);
    fixture.componentRef.setInput('selectedKey', 'nd_build:1');
    const picks: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => picks.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="history-step-efficiency"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="history-step-cost"]')).toBeNull();
    fixture.componentRef.setInput('costOnly', true);
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="history-step-efficiency"]')).toBeNull();
    expect(el.querySelector('[data-testid="history-step-cost"]')?.textContent).toContain('$0.42');
    el.querySelector<HTMLButtonElement>('button.selected')!.click();
    expect(picks).toEqual([null]);
  });

  it('keeps an unknown JSON diagnostic in the visible bounce and its hover', async () => {
    const raw = '{"error":"permission denied","code":403}';
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', chunkDetail({ status: 'done', history: [], bounces: [bounceView({ envelope: raw })] }));
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="history-bounce-step"]');
    expect(row?.getAttribute('title')).toBe(raw);
    expect(row?.querySelector('[data-testid="history-choice"]')?.textContent).toBe(raw);
  });
});

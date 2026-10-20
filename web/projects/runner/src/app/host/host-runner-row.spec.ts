import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { HostRunnerRow } from './host-runner-row.model';
import { HostRunnerRowView } from './host-runner-row';

const ROW = (overrides: Partial<HostRunnerRow> = {}): HostRunnerRow => ({
  key: 'rn_1',
  runnerId: 'rn_1',
  name: 'r-claude',
  ref: '3YJ9',
  tone: 'green',
  halted: false,
  summary: 'claude code · 4 agents · 1 busy',
  lastTickAt: '2026-07-16T11:59:26.000Z',
  bindings: [{ key: 'console@ws', projectName: 'Console', color: 'amber', workspaceId: 'ws', used: 1, agents: 4 }],
  spends: ['Claude Max'],
  harnesses: [
    { harnessId: 'claude_code', name: 'claude code', version: '2.1.0', available: true, admitted: null, cause: null, degradations: [] },
  ],
  gates: [],
  hub: { endpoint: 'http://hub', reachable: true, lastContactAt: null, buffered: 2, refusal: null, hubVersion: null, window: null },
  problems: [],
  state: [{ label: 'WORKING', tone: 'running', title: null }],
  heldPending: false,
  held: [
    {
      chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      ref: 'N3YJ9',
      projectName: 'Console',
      color: 'amber',
      node: 'build',
      status: { label: 'RUNNING', tone: 'running' },
    },
  ],
  ...overrides,
});

describe('HostRunnerRowView', () => {
  async function render(row: HostRunnerRow): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [HostRunnerRowView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture: ComponentFixture<HostRunnerRowView> = TestBed.createComponent(HostRunnerRowView);
    fixture.componentRef.setInput('row', row);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: HTMLElement, testid: string) => el.querySelector(`[data-testid="${testid}"]`)?.textContent?.replace(/\s+/g, ' ').trim();

  it('renders the runner’s name, summary and state badge', async () => {
    const el = await render(ROW());

    expect(text(el, 'host-runner-name')).toBe('r-claude');
    expect(text(el, 'host-runner-summary')).toBe('claude code · 4 agents · 1 busy');
    expect(text(el, 'host-runner-state')).toContain('WORKING');
    expect(el.querySelector('[data-testid="host-runner"]')?.getAttribute('data-runner-id')).toBe('rn_1');
  });

  it('renders each binding with its project, workspace and agents in use', async () => {
    const el = await render(ROW());

    const binding = el.querySelector<HTMLElement>('[data-testid="host-runner-binding"]')!;
    expect(binding.querySelector('.project')?.textContent?.trim()).toBe('Console');
    expect(binding.querySelector('fleet-kit-swatch')).not.toBeNull();
    expect(binding.querySelector('.ws')?.textContent?.trim()).toBe('ws');
    expect(binding.querySelector('.agents')?.textContent?.trim()).toBe('1 / 4 agents');
    expect(text(el, 'host-runner-spends')).toContain('Claude Max');
  });

  it('links each held chunk to the board, opened on that chunk', async () => {
    const el = await render(ROW());

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="host-runner-chunk"]')!;
    expect(link.textContent).toContain('N3YJ9');
    expect(link.getAttribute('href')).toBe('/board?chunk=ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('says it holds no chunks when it holds none', async () => {
    const el = await render(ROW({ held: [] }));

    expect(el.querySelector('[data-testid="host-runner-no-chunks"]')).not.toBeNull();
  });

  it('renders the hub link, its refusal and its buffered facts', async () => {
    const el = await render(
      ROW({
        hub: {
          endpoint: 'http://hub',
          reachable: false,
          lastContactAt: null,
          buffered: 2,
          refusal: { label: 'REFUSED', tone: 'needs', title: null },
          hubVersion: null,
          window: null,
        },
      }),
    );

    const hub = text(el, 'host-runner-hub');
    expect(hub).toContain('UNREACHABLE');
    expect(hub).toContain('REFUSED');
    expect(hub).toContain('2 buffered');
  });

  it('names the hub\'s version and the window it admits, and nothing before a registration answers', async () => {
    const hub = { endpoint: 'http://hub', reachable: true, lastContactAt: null, buffered: 0, refusal: null };
    const answered = await render(ROW({ hub: { ...hub, hubVersion: '0.3.0', window: 'admits 0.2 – 0.3' } }));
    expect(text(answered, 'host-runner-hub-version')).toBe('· hub 0.3.0 admits 0.2 – 0.3');

    TestBed.resetTestingModule();
    const unanswered = await render(ROW({ hub: { ...hub, hubVersion: null, window: null } }));
    expect(unanswered.querySelector('[data-testid="host-runner-hub-version"]')).toBeNull();
  });

  it('shows a halted runner’s state and reasons only', async () => {
    const el = await render(
      ROW({ halted: true, tone: 'red', state: [{ label: 'HALTED', tone: 'needs', title: 'no hub token' }], hub: null, held: [] }),
    );

    expect(el.textContent).toContain('HALTED');
    expect(el.textContent).toContain('no hub token');
    expect(el.querySelector('[data-testid="host-runner-binds"]')).toBeNull();
    expect(el.querySelector('[data-testid="host-runner-chunks"]')).toBeNull();
  });

  it('neither says it holds no chunks nor counts them while the leases load', async () => {
    const el = await render(ROW({ held: [], state: [], heldPending: true }));

    expect(el.querySelector('[data-testid="host-runner-no-chunks"]')).toBeNull();
    expect(el.querySelector('.held-hd')?.textContent?.trim()).toBe('Held chunks');
    expect(el.querySelector('.held')?.textContent).toContain('LOADING');
  });

  it('says it holds no chunks and is IDLE once the leases land empty', async () => {
    const el = await render(ROW({ held: [], state: [{ label: 'IDLE', tone: 'idle', title: null }] }));

    expect(el.querySelector('[data-testid="host-runner-no-chunks"]')).not.toBeNull();
    expect(text(el, 'host-runner-state')).toContain('IDLE');
  });
});

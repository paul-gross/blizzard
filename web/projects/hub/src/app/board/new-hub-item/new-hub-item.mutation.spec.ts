import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { stubHubClient, type RequestClientStub } from 'fleet/testing';
import { injectCreateHubItemMutation } from './new-hub-item.mutations';
import { CREATED_ITEM } from './new-hub-item.test-data.translate';

@Component({ selector: 'app-test-create-hub-item-host', template: '', changeDetection: ChangeDetectionStrategy.OnPush })
class CreateHubItemHost {
  readonly mutation = injectCreateHubItemMutation();
}

describe('injectCreateHubItemMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  const vars = { project: 'project-id', title: 'Title', body: 'Body', urgency: 'urgent-id' };

  beforeEach(() => {
    stub = stubHubClient({ 'POST /api/work-sources/{source}/items': () => CREATED_ITEM });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('creates on the hub source with project and urgency and resolves to the minted chunk', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectCreateHubItemMutation());

    await expect(mutation.mutateAsync(vars)).resolves.toBe('ch_created');

    const calls = stub.forRoute('/api/work-sources/hub/items', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ title: 'Title', body: 'Body', urgency: 'urgent-id' });
    expect(new URLSearchParams(calls[0].search).get('project')).toBe('project-id');
  });

  it('invalidates the chunk list and the backlog', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateHubItemMutation());

    await mutation.mutateAsync(vars);

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toEqual([
      ['hub', 'chunks'],
      ['hub', 'backlog'],
    ]);
  });

  it('stays pending until the invalidations resolve', async () => {
    let release!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(new Promise<void>((resolve) => (release = resolve)));
    const fixture = TestBed.createComponent(CreateHubItemHost);
    fixture.detectChanges();
    const { mutation } = fixture.componentInstance;

    const done = mutation.mutateAsync(vars);
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    expect(stub.requests).toHaveLength(1);
    expect(mutation.isPending()).toBe(true);

    release();
    await done;
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    expect(mutation.isPending()).toBe(false);
  });
});

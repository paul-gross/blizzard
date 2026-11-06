import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { gardenWorkItemView, settle, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { injectHubWorkItemQuery, injectHubWorkItemsQuery, type WorkItemPointer } from './work-item.query';

@Component({
  selector: 'app-test-work-item-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestWorkItemQueryHost {
  readonly source = signal<string | null>(null);
  readonly ref = signal<string | null>(null);
  readonly query = injectHubWorkItemQuery(
    () => this.source(),
    () => this.ref(),
  );
}

@Component({
  selector: 'app-test-work-items-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestWorkItemsQueryHost {
  readonly pointers = signal<readonly WorkItemPointer[]>([]);
  readonly query = injectHubWorkItemsQuery(() => this.pointers());
}

function workItem(source: string, ref: string, label: string) {
  return gardenWorkItemView({ source, ref, label });
}

describe('injectHubWorkItemQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('stays disabled while the source/ref pair is incomplete', async () => {
    stub = stubHubClient({});
    TestBed.configureTestingModule({
      imports: [TestWorkItemQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemQueryHost);
    fixture.componentInstance.source.set('hub');
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.forRoute('/api/work-sources/hub/items/42', 'GET')).toHaveLength(0);
  });

  it('reads one work item off GET /api/work-sources/{source}/items/{ref}', async () => {
    stub = stubHubClient({
      'GET /api/work-sources/{source}/items/{ref}': () => gardenWorkItemView({ web_url: '/board/chunk/ch_1' }),
    });
    TestBed.configureTestingModule({
      imports: [TestWorkItemQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemQueryHost);
    fixture.componentInstance.source.set('hub');
    fixture.componentInstance.ref.set('42');
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.label).toBe('hub#42');
    expect(stub.forRoute('/api/work-sources/hub/items/42', 'GET')).toHaveLength(1);
  });
});

describe('injectHubWorkItemsQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('stays disabled for an empty pointer list', async () => {
    stub = stubHubClient({});
    TestBed.configureTestingModule({
      imports: [TestWorkItemsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemsQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isPending()).toBe(true);
    expect(stub.requests).toHaveLength(0);
  });

  it('reads every pointer independently, live, and joins the results', async () => {
    stub = stubHubClient({
      'GET /api/work-sources/{source}/items/{ref}': (_request, { source, ref }) => workItem(source, ref, `${source}#${ref}`),
    });
    TestBed.configureTestingModule({
      imports: [TestWorkItemsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemsQueryHost);
    fixture.componentInstance.pointers.set([
      { source: 'hub', ref: '1' },
      { source: 'hub', ref: '2' },
    ]);
    await settle(fixture);

    const data = fixture.componentInstance.query.data();
    expect(data?.map((w) => w.label).sort()).toEqual(['hub#1', 'hub#2']);
    expect(stub.forRoute('/api/work-sources/hub/items/1', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/work-sources/hub/items/2', 'GET')).toHaveLength(1);
  });

  it("drops a failed pointer's row rather than failing the whole join", async () => {
    stub = stubHubClient({
      'GET /api/work-sources/{source}/items/{ref}': (_request, { source, ref }) =>
        ref === '1' ? workItem(source, ref, 'hub#1') : stubError(404, { detail: 'not found' }),
    });
    TestBed.configureTestingModule({
      imports: [TestWorkItemsQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestWorkItemsQueryHost);
    fixture.componentInstance.pointers.set([
      { source: 'hub', ref: '1' },
      { source: 'hub', ref: '2' },
    ]);
    await settle(fixture);

    const data = fixture.componentInstance.query.data();
    expect(data?.map((w) => w.label)).toEqual(['hub#1']);
    expect(fixture.componentInstance.query.isError()).toBe(false);
  });
});

import { ChangeDetectionStrategy, Component, computed, inject, resource } from '@angular/core';
import { RouterLink } from '@angular/router';
import { injectRunnerHostNames } from 'fleet/core';
import { KitBackBar } from 'fleet/kit';
import { ViewportService } from 'fleet/viewport';
import { AppLink } from 'fleet/shell';
import { injectQueryFilters, injectRouteParam } from '../core/route-state';
import { injectHostFactsQuery } from '../core/hosts.query';
import { EventDetailView } from './event-detail-view';
import { detailState, findEvent } from './event-detail.model';
import { narrowEventSeverity } from './event-log.domain';
import { injectHubEventsQuery } from './events.query';

@Component({
  selector: 'app-event-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventDetailView, KitBackBar, AppLink, RouterLink],
  templateUrl: './event-detail-page.html',
  styleUrl: './event-detail-page.css',
})
export class EventDetailPage {
  private readonly filters = injectQueryFilters();
  private readonly id = injectRouteParam('eventId');
  private readonly query = injectHubEventsQuery(() => ({
    severity: narrowEventSeverity(this.filters.read('severity') ?? ''),
    runnerId: this.filters.read('runner'),
    chunkId: this.filters.read('chunk'),
  }));
  protected readonly hosts = injectRunnerHostNames();
  private readonly hostsQuery = injectHostFactsQuery();
  protected readonly hostMemberships = computed(() => this.hostsQuery.data() ?? []);
  protected readonly viewport = inject(ViewportService);
  private readonly resolved = resource({
    params: () => ({
      events: this.query.data() ?? [], id: this.id(), at: this.filters.read('at'), kind: this.filters.read('kind'),
      chunk: this.filters.read('eventChunk'), fingerprint: this.filters.read('fingerprint'),
    }),
    loader: ({ params }) => findEvent(params.events, params.id, params.at, params.kind, params.chunk, params.fingerprint),
  });
  protected readonly event = computed(() => this.resolved.value() ?? null);
  protected readonly state = computed(() => detailState(this.query, this.resolved.isLoading(), this.event()));
  protected readonly backParams = computed(() => ({
    severity: this.filters.read('severity'), runner: this.filters.read('runner'), chunk: this.filters.read('chunk'),
  }));
}

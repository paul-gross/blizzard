import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { map } from 'rxjs';

import {
  type KitAsyncStateValue,
  injectHubChunkDetailQuery,
  injectHubChunkWorkItemsQuery,
  restingAsyncState,
} from 'fleet';

import { injectProjectsQuery } from '../../core/projects.query';
import { injectHubClock } from './hub-clock';
import { openWorkItems } from '../chunk-dock/chunk-detail.model';
import { injectLeaseTranscriptQuery } from './lease-transcript.query';
import { type StepRef, stepRefOfParams, stepRefParams } from './step.model';
import { injectHubEnvTimelineStepQuery } from './step.query';
import {
  chunkFor,
  drawerNow,
  drawerOrigin,
  ownArtifactKeys,
  placedOf,
  projectOf,
  stepArtifactsOf,
  transcriptRefOf,
} from './step-drawer.model';
import { StepDrawerView } from './step-drawer-view';

/**
 * The Tracks layout's drawer: reads the node step the URL's `?chunk=` and `?step=` or `?at=` name, with its chunk, the
 * chunk's work items and the session's transcript, and hands {@link StepDrawerView} the lot. A pick along the chunk's path, or the
 * close button, writes the URL.
 */
@Component({
  selector: 'app-step-drawer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StepDrawerView],
  templateUrl: './step-drawer.html',
  styleUrl: './step-drawer.css',
  host: { '[class.closed]': 'key() === null' },
})
export class StepDrawer {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  /** The step the URL names — the board's selected chunk, at `?step=` or `?at=`, or its newest step. */
  protected readonly key = toSignal(this.route.queryParamMap.pipe(map(stepRefOfParams)), {
    initialValue: stepRefOfParams(this.route.snapshot.queryParamMap),
  });
  private readonly query = injectHubEnvTimelineStepQuery(() => this.key());
  /** The step read's answer — `undefined` too for a chunk with no node step yet. */
  protected readonly view = computed(() => this.query.data() ?? undefined);
  /** `empty` for a chunk with no node step yet, as for none selected. */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.query, this.query.data() === null),
  );
  /** The hub's now, ticking on between reads — a running step's span, path and end grow with it. */
  protected readonly nowMs = injectHubClock(() => drawerNow(this.view(), 0));

  /** The step the URL names, off the chunk's path — on screen at once while its own detail still reads. */
  protected readonly placed = computed(() => placedOf(this.view(), this.key()));
  /** Whether the step's own detail — its spend, model and escalations — is still reading; the path shows meanwhile. */
  protected readonly stepPending = computed(() => this.query.isPlaceholderData());

  private readonly projects = injectProjectsQuery();
  protected readonly project = computed(() => projectOf(this.projects.data(), this.placed()?.segment.project_id));

  /** The step's chunk, read as the chunk drawer reads it — the reference column, and the artifacts' source. */
  private readonly chunkId = computed(() => this.key()?.chunkId ?? null);
  private readonly chunkQuery = injectHubChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectHubChunkWorkItemsQuery(() => this.chunkId());
  protected readonly chunk = computed(() => chunkFor(this.chunkQuery.data(), this.chunkId()));
  protected readonly workItems = computed(() => openWorkItems(this.chunkId(), this.workItemsQuery));
  /** The step's own artifacts, once its own detail is read — not the previous step's held over. */
  protected readonly stepArtifacts = computed(() =>
    stepArtifactsOf(this.chunk(), ownArtifactKeys(this.view(), this.stepPending())),
  );

  /** The transcript of the session that ran the step. */
  private readonly transcriptRef = computed(() => transcriptRefOf(this.placed()?.segment));
  protected readonly transcript = injectLeaseTranscriptQuery(() => this.transcriptRef());
  /** `empty` for a step with no transcript to read. */
  protected readonly transcriptState = computed(() => restingAsyncState(this.transcriptRef() === null, this.transcript, false));

  /** Where the chunk page's back link returns: this view, on this step. */
  protected readonly origin = computed(() => drawerOrigin(this.route.snapshot.queryParamMap.get('layout'), this.placed()));

  protected select(ref: StepRef): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: stepRefParams(ref), queryParamsHandling: 'merge' });
  }

  protected close(): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: stepRefParams(null), queryParamsHandling: 'merge' });
  }
}

import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { map } from 'rxjs';
import { QueryClient } from '@tanstack/angular-query-experimental';
import { type KitAsyncStateValue } from 'fleet';
import { injectShowProjectColors } from '../../core/project-lens-colors';
import { injectHubBoardChunksQuery } from '../../core/chunks.query';
import { injectProjectsQuery } from '../../core/projects.query';

import { NO_TIMELINE, hubNow } from './env-timeline.model';
import { injectHubClock } from './hub-clock';
import { injectHubEnvTimelineLiveQuery, injectHubEnvTimelineNowQuery, injectHubEnvTimelineRangeQuery } from './env-timeline.query';
import { type StepKey, type StepRef, stepRefOfParams, stepRefParams } from './step.model';
import { injectHubEnvTimelineStepQuery, stepQueryOptions } from './step.query';
import {
  buildTracks,
  foldBelowMs,
  highlightedStep,
  presentRows,
  progressByChunk,
  projectHuesOf,
  tracksState,
  windowCovered,
  windowData,
} from './tracks.model';
import {
  DEFAULT_TRACK_WINDOW,
  type TrackWindow,
  liveCoverFor,
  rangeFor,
  resolveTrackWindow,
  spanChoice,
  windowForSpan,
  windowFromRange,
} from './tracks-window.model';
import { TracksView } from './tracks-view';

/** The narrowest a node step is drawn on its own; narrower ones fold with their chunk's neighbours. */
const MIN_BLOCK_PX = 4;

/** The board's Tracks layout: holds the window, reads the env timeline for it, and hands {@link TracksView} the rows. */
@Component({
  selector: 'app-tracks-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TracksView],
  templateUrl: './tracks-panel.html',
  styleUrl: './tracks-panel.css',
})
export class TracksPanel {
  private readonly window = signal<TrackWindow>(DEFAULT_TRACK_WINDOW);
  /** The hub's now, ticking on between answers — the now line and every open step end there, not at the browser
   * clock. */
  protected readonly now = injectHubClock(() =>
    Math.max(hubNow(this.liveQuery.data(), 0), hubNow(this.nowQuery.data(), 0)),
  );
  private readonly nowMinute = computed(() => Math.floor(this.now() / 60_000) * 60_000);

  protected readonly resolved = computed(() => resolveTrackWindow(this.window(), this.nowMinute()));

  /** The span the live read covers — it widens with the widest window followed, and never narrows, so a shorter chip
   * or a short pan back reads from data already here. */
  private readonly liveCover = signal(liveCoverFor(DEFAULT_TRACK_WINDOW.spanMs));
  private readonly liveQuery = injectHubEnvTimelineLiveQuery(() => this.liveCover());
  /** Whether the window on screen lies inside the live read — always, while following now. */
  private readonly covered = computed(() => windowCovered(this.resolved(), this.liveQuery.data()));
  private readonly rangeQuery = injectHubEnvTimelineRangeQuery(() =>
    this.covered() ? null : rangeFor(this.resolved().startMs, this.resolved().endMs),
  );
  private readonly data = computed(
    () => windowData(this.covered(), this.liveQuery.data(), this.rangeQuery.data()) ?? NO_TIMELINE,
  );
  /** What each environment holds now, read apart from the window so the present column never follows a pan. */
  private readonly nowQuery = injectHubEnvTimelineNowQuery();
  private readonly live = computed(() => this.nowQuery.data());

  /** Under the all-projects lens each block wears its project's colour; under one project's lens, its runner's. */
  private readonly showProjectColors = injectShowProjectColors();
  private readonly projects = injectProjectsQuery();
  /** The board's own chunk list — already read for the board — for each running chunk's node strip. */
  private readonly boardChunks = injectHubBoardChunksQuery();
  protected readonly progress = computed(() => progressByChunk(this.boardChunks.data()?.chunks));
  private readonly projectHues = computed(() => projectHuesOf(this.showProjectColors(), this.projects.data()));

  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  /** The step the URL names — the board's selected chunk, at `?step=` or `?at=`, or its newest step. */
  private readonly stepRef = toSignal(this.route.queryParamMap.pipe(map(stepRefOfParams)), {
    initialValue: stepRefOfParams(this.route.snapshot.queryParamMap),
  });
  /** The drawer's read of that step, shared through the query cache — it resolves a session or the newest step to the
   * block it is. */
  private readonly stepQuery = injectHubEnvTimelineStepQuery(() => this.stepRef());
  /** The step the drawer shows, highlighted on the tracks with the rest of its chunk — the whole chunk alone until its
   * step is read. */
  private readonly selected = computed<StepKey | null>(() =>
    highlightedStep(this.stepRef(), this.stepQuery.data()?.step.segment),
  );

  /** How many ms a pixel of the tracks spans now, from the view. */
  private readonly msPerPx = signal(0);
  /** Steps narrower than this fold together. */
  private readonly minBlockMs = computed(() => foldBelowMs(this.msPerPx(), MIN_BLOCK_PX));

  protected readonly tracks = computed(() =>
    buildTracks(this.data(), this.live(), this.nowMinute(), this.projectHues(), this.selected(), this.minBlockMs()),
  );

  protected onScale(msPerPx: number): void {
    this.msPerPx.set(msPerPx);
  }

  protected selectStep(ref: StepRef): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: stepRefParams(ref), queryParamsHandling: 'merge' });
  }
  protected readonly present = computed(() =>
    presentRows(this.data(), this.live(), this.now(), this.tracks().runnerHues),
  );
  protected readonly span = computed(() => spanChoice(this.window()));
  protected readonly state = computed<KitAsyncStateValue>(() =>
    tracksState(this.covered(), this.liveQuery, this.rangeQuery, this.data(), this.live()),
  );

  private readonly queryClient = inject(QueryClient);

  /** A step the pointer rests on: read its detail ahead, so a click opens the drawer already filled. */
  protected prefetchStep(ref: StepRef): void {
    void this.queryClient.prefetchQuery(stepQueryOptions(ref));
  }

  private setWindow(window: TrackWindow): void {
    this.window.set(window);
    if (window.endMs === null) this.liveCover.update((cover) => Math.max(cover, liveCoverFor(window.spanMs)));
  }

  protected chooseSpan(value: string): void {
    this.setWindow(windowForSpan(value, this.window()));
  }

  protected onRange(startMs: number, endMs: number): void {
    this.setWindow(windowFromRange(startMs, endMs, this.now()));
  }

  protected followNow(): void {
    this.setWindow({ spanMs: this.window().spanMs, endMs: null });
  }
}

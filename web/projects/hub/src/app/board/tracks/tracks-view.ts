import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  type ElementRef,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitButton, KitChips, type KitChipOption } from 'fleet';
import type { DataSet } from 'vis-data';
import type { DataItem, Timeline, TimelineOptions } from 'vis-timeline/standalone';

import { NodeStrip } from '../node-strip/node-strip';
import type { NodeBlock } from '../node-strip/node-strip.model';
import type { StepRef } from './step.model';
import type { PresentRow, Tracks } from './tracks.model';
import { TRACK_SPANS } from './tracks-window.model';

/** The lazily loaded stylesheet bundle (angular.json `bundleName`) carrying vis-timeline's base CSS and the board's theme over it. */
const TIMELINE_STYLESHEET = 'tracks-timeline.css';

const NOW_LINE = 'now';

/** The most runners the bar's key names; past this the key is left off. */
const RUNNER_KEY_MAX = 5;

/** How long a pan or zoom must rest before the window it landed on is handed up — a wheel zoom fires on every tick. */
const RANGE_SETTLE_MS = 200;

/** How long the pointer rests on a block before its detail is read ahead — the tooltip's own delay. */
const HOVER_PREFETCH_MS = 150;

const SPAN_CHIPS: readonly KitChipOption[] = TRACK_SPANS.map((span) => ({
  value: span.value,
  label: span.label,
  testid: `tracks-span-${span.value}`,
}));

/** Where a row sits in the scroll body, so the present column lines up with it. */
interface RowBox {
  readonly top: number;
  readonly height: number;
}

/**
 * The tracks: each runner host's environments as rows over time, one block per node step a chunk spent there, with
 * the present column beside the now line naming each environment's current occupant.
 *
 * Presentational: the window and data come in, a preset chip or a pan/zoom goes out. vis-timeline is imported on
 * first render, so its bundle and stylesheet load only when the Tracks layout is opened.
 */
@Component({
  selector: 'app-tracks-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitChips, NodeStrip],
  templateUrl: './tracks-view.html',
  styleUrl: './tracks-view.css',
})
export class TracksView {
  readonly tracks = input.required<Tracks>();
  readonly present = input.required<readonly PresentRow[]>();
  /** Each chunk's progress through its graph, by chunk id — the board card's node strip, under its present line. */
  readonly progress = input<ReadonlyMap<string, readonly NodeBlock[]>>(new Map());
  readonly startMs = input.required<number>();
  readonly endMs = input.required<number>();
  readonly live = input(true);
  /** Where the now line stands — the hub's now. */
  readonly nowMs = input.required<number>();
  readonly span = input<string | null>(null);
  readonly state = input<KitAsyncStateValue>('ready');

  /** A preset chip's value. */
  readonly chooseSpan = output<string>();
  /** The window a user's pan or zoom settled on. */
  readonly rangeChange = output<{
    readonly startMs: number;
    readonly endMs: number;
  }>();
  /** How many ms one pixel of the tracks spans — on every zoom and resize. */
  readonly scaleChange = output<number>();
  /** A node step picked on the tracks. */
  readonly selectStep = output<StepRef>();
  /** A node step the pointer has rested on — worth reading ahead. */
  readonly hoverStep = output<StepRef>();
  /** Back to following now at the current span. */
  readonly followNow = output<void>();

  protected readonly spanChips = SPAN_CHIPS;
  /** The runner key — each runner's name and hue — while blocks are coloured by runner, and only while the runners are
   * few enough to name inline; a larger fleet leaves the key off rather than wrapping the bar. */
  protected readonly runnerKey = computed(() => {
    const hues = [...(this.tracks().runnerHues ?? [])];
    return hues.length <= RUNNER_KEY_MAX ? hues.map(([name, hue]) => ({ name, hue })) : [];
  });
  protected readonly rowBoxes = signal<ReadonlyMap<string, RowBox>>(new Map());

  private readonly host = viewChild.required<ElementRef<HTMLElement>>('timeline');
  private readonly body = viewChild.required<ElementRef<HTMLElement>>('body');
  private readonly document = inject(DOCUMENT);
  private readonly timeline = signal<Timeline | null>(null);
  private items: DataSet<DataItem> | null = null;
  /** Each block on the timeline now, by id, with the signature it was drawn from — only a block whose signature
   * moved is redrawn. */
  private drawn = new Map<string, string>();
  /** The step each block on the timeline opens, by block id — kept beside the dataset, so a pick or a hover reads it
   * back rather than parsing an id. */
  private steps = new Map<string, StepRef>();
  private drawnGroups = '';
  private measureFrame = 0;
  private hoverTimer: ReturnType<typeof setTimeout> | undefined;
  private rangeTimer: ReturnType<typeof setTimeout> | undefined;
  /** The width of the tracks' time panel, in px. */
  private centerWidth = 0;

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      this.loadStylesheet();
      void import('vis-timeline/standalone').then((vis) => {
        if (destroyRef.destroyed) return;
        // The standalone bundle ships its own DataSet at runtime; its declarations leave it to vis-data's types.
        const { Timeline: TimelineCtor, DataSet: DataSetCtor } = vis as typeof vis & { DataSet: typeof DataSet };
        this.items = new DataSetCtor<DataItem>();
        const timeline = new TimelineCtor(this.host().nativeElement, this.items, this.options());
        // vis redraws a pan or zoom itself; the window it lands on goes up once it rests, so the board's state, reads and
        // blocks follow the gesture's end rather than every frame or wheel tick of it.
        timeline.on('rangechanged', (props: { start: Date; end: Date; byUser: boolean }) => {
          this.emitScale(props.end.valueOf() - props.start.valueOf());
          if (!props.byUser) return;
          clearTimeout(this.rangeTimer);
          this.rangeTimer = setTimeout(
            () => this.rangeChange.emit({ startMs: props.start.valueOf(), endMs: props.end.valueOf() }),
            RANGE_SETTLE_MS,
          );
        });
        // Rows move only when they are redrawn, collapsed or resized — never on a pan — so they are measured then.
        const resize = new ResizeObserver(() => {
          const center = this.host().nativeElement.querySelector<HTMLElement>('.vis-panel.vis-center');
          this.centerWidth = center?.clientWidth ?? 0;
          const window = timeline.getWindow();
          this.emitScale(window.end.valueOf() - window.start.valueOf());
          this.scheduleMeasure();
        });
        resize.observe(this.host().nativeElement);
        const labels = this.host().nativeElement.querySelector('.vis-labelset');
        if (labels !== null) resize.observe(labels);
        timeline.on('itemover', (props: { item: string | number }) => {
          clearTimeout(this.hoverTimer);
          const key = this.steps.get(String(props.item));
          if (key !== undefined) this.hoverTimer = setTimeout(() => this.hoverStep.emit(key), HOVER_PREFETCH_MS);
        });
        timeline.on('itemout', () => clearTimeout(this.hoverTimer));
        timeline.on('click', (props: { item: string | number | null }) => {
          const key = props.item === null ? undefined : this.steps.get(String(props.item));
          if (key !== undefined) this.selectStep.emit(key);
        });
        timeline.addCustomTime(this.nowMs(), NOW_LINE);
        destroyRef.onDestroy(() => {
          clearTimeout(this.hoverTimer);
          clearTimeout(this.rangeTimer);
          cancelAnimationFrame(this.measureFrame);
          resize.disconnect();
          timeline.destroy();
        });
        this.timeline.set(timeline);
      });
    });

    effect(() => {
      const timeline = this.timeline();
      if (timeline === null) return;
      const { groups, items } = this.tracks();
      this.drawGroups(timeline, groups);
      this.drawItems(items);
    });

    effect(() => {
      this.timeline()?.setCustomTime(this.nowMs(), NOW_LINE);
    });

    effect(() => {
      const timeline = this.timeline();
      if (timeline === null) return;
      const start = this.startMs();
      const end = this.endMs();
      const shown = timeline.getWindow();
      if (Math.abs(shown.start.valueOf() - start) > 1000 || Math.abs(shown.end.valueOf() - end) > 1000) {
        timeline.setWindow(start, end, { animation: false });
      }
    });
  }

  /** Redraw the rows only when one is added, dropped, renamed or reordered — a tick or a selection moves none. */
  private drawGroups(timeline: Timeline, groups: Tracks['groups']): void {
    const signature = JSON.stringify(groups);
    if (signature === this.drawnGroups) return;
    this.drawnGroups = signature;
    this.scheduleMeasure();
    timeline.setGroups(
      groups.map((group) => ({
        id: group.id,
        content: this.groupLabel(group.id, group.label, group.detail),
        className: `tk-${group.kind}`,
        order: group.order,
        ...(group.nestedGroups ? { nestedGroups: [...group.nestedGroups], showNested: true } : {}),
      })),
    );
  }

  /** Hand vis only the blocks that changed: a minute's tick moves the open steps' ends, a selection a few classes. */
  private drawItems(items: Tracks['items']): void {
    const dataset = this.items;
    if (dataset === null) return;
    const next = new Map<string, string>();
    const steps = new Map<string, StepRef>();
    const changed: DataItem[] = [];
    for (const { step, ...item } of items) {
      const signature = `${item.group}|${item.start}|${item.end}|${item.content}|${item.className}|${item.style ?? ''}|${item.title}`;
      next.set(item.id, signature);
      steps.set(item.id, step);
      if (this.drawn.get(item.id) !== signature) changed.push({ ...item, type: 'range' });
    }
    const gone = [...this.drawn.keys()].filter((id) => !next.has(id));
    if (gone.length > 0) dataset.remove(gone);
    if (changed.length > 0) dataset.update(changed);
    this.drawn = next;
    this.steps = steps;
  }

  private emitScale(spanMs: number): void {
    if (this.centerWidth > 0) this.scaleChange.emit(spanMs / this.centerWidth);
  }

  /** Measure once per frame however many changes ask for it. */
  private scheduleMeasure(): void {
    if (this.measureFrame !== 0) return;
    this.measureFrame = requestAnimationFrame(() => {
      this.measureFrame = 0;
      this.measureRows();
    });
  }

  protected boxOf(groupId: string): RowBox | undefined {
    return this.rowBoxes().get(groupId);
  }

  private options(): TimelineOptions {
    return {
      stack: false,
      orientation: { axis: 'top', item: 'top' },
      groupHeightMode: 'fixed',
      showCurrentTime: false,
      zoomMin: 10 * 60_000,
      zoomMax: 14 * 24 * 3_600_000,
      start: this.startMs(),
      end: this.endMs(),
      selectable: false,
      // Unstacked, vis sets each block half the vertical margin down its row: 6px above, and the CSS height leaves 6px below.
      margin: { item: { horizontal: 0, vertical: 12 }, axis: 0 },
      tooltip: { followMouse: true, overflowMethod: 'cap', delay: 150 },
      groupOrder: 'order',
    };
  }

  /** A group label carrying its id, so {@link measureRows} can find the row it heads. */
  private groupLabel(id: string, label: string, detail: string): HTMLElement {
    const el = this.document.createElement('div');
    el.className = 'tk-label';
    el.dataset['gid'] = id;
    const name = this.document.createElement('span');
    name.className = 'tk-label-name';
    name.textContent = label;
    el.append(name);
    if (detail) {
      const sub = this.document.createElement('span');
      sub.className = 'tk-label-detail';
      sub.textContent = detail;
      el.append(sub);
    }
    return el;
  }

  /** Read where vis laid each row out, relative to the scroll body, for the present column. */
  private measureRows(): void {
    const origin = this.body().nativeElement.getBoundingClientRect();
    const scrollTop = this.body().nativeElement.scrollTop;
    const boxes = new Map<string, RowBox>();
    for (const el of this.host().nativeElement.querySelectorAll<HTMLElement>('[data-gid]')) {
      const row = el.closest<HTMLElement>('.vis-label');
      if (row === null) continue;
      const rect = row.getBoundingClientRect();
      boxes.set(el.dataset['gid'] ?? '', {
        top: rect.top - origin.top + scrollTop,
        height: rect.height,
      });
    }
    const previous = this.rowBoxes();
    const same = previous.size === boxes.size && [...boxes].every(([id, box]) => previous.get(id)?.top === box.top && previous.get(id)?.height === box.height);
    if (!same) this.rowBoxes.set(boxes);
  }

  private loadStylesheet(): void {
    if (this.document.querySelector(`link[data-tracks-timeline]`) !== null) return;
    const link = this.document.createElement('link');
    link.rel = 'stylesheet';
    link.href = TIMELINE_STYLESHEET;
    link.dataset['tracksTimeline'] = '';
    this.document.head.append(link);
  }
}

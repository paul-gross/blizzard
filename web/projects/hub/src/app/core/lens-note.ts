import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { lensNoteText } from './lens-note.model';
import { injectLensedProject } from './lensed-project';

/**
 * The note a tenant-wide view shows while the project lens is on a project: the lens narrows
 * nothing here, and the note says so rather than leaving the view to look filtered. On All it
 * renders nothing.
 */
@Component({
  selector: 'app-lens-note',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './lens-note.html',
  styleUrl: './lens-note.css',
})
export class LensNote {
  private readonly project = injectLensedProject();

  /** What the view shows, as the sentence's subject — "Graphs", "Members", "Secrets". */
  readonly surface = input.required<string>();

  protected readonly text = computed(() => lensNoteText(this.surface(), this.project()));
}

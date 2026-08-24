import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { parseMarkdown } from './markdown-parse';

/**
 * A work-item body's bounded-subset markdown, rendered (blizzard#362) — headings,
 * paragraphs, fenced/inline code, bullet/ordered lists, links, bold, and italic; anything
 * outside that subset renders as its literal source text. Presentational and input-only,
 * so a caller hands it raw text and nothing more (`bzh:frontend-kit-floor`).
 *
 * Renders through the template's own interpolation — never `innerHTML`, never
 * `bypassSecurityTrust*` — so Angular's default escaping is the one thing standing between
 * a body and the DOM; raw HTML in the source is inert text, not markup. A link's `href` is
 * additionally scheme-allowlisted by the parser itself ({@link parseMarkdown}), rather than
 * resting on binding sanitization alone.
 */
@Component({
  selector: 'fleet-kit-markdown',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgTemplateOutlet],
  templateUrl: './kit-markdown.html',
  styleUrl: './kit-markdown.css',
})
export class KitMarkdown {
  readonly text = input.required<string>();

  protected readonly blocks = computed(() => parseMarkdown(this.text()));
}

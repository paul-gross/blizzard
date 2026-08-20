import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/**
 * A single collapsible section — a header button toggling a
 * projected body's visibility, the shared building block behind the node history
 * tab's Transcripts/Artifacts panels.
 *
 * Fully controlled, the same shape {@link KitTabs} already uses for its own
 * `activeValue`/`choose` pair (`bzh:frontend-container-presentational`): the consumer
 * owns the open/closed signal and this component only renders it and reports a
 * toggle. No internal state, so more than one section reads open at once with
 * nothing here coordinating that — a consumer wanting single-open composes several
 * of these against one shared signal itself, this component does not pick a side.
 *
 * The header is projected (`[accordionHeader]`), not a `label`/`count` input pair —
 * a plain uppercase tag label and a richer title both need to fit through the same
 * slot without a second, parallel API for the richer shape. A second slot,
 * `[accordionAside]`, sits beside the trigger `<button>` rather than inside it — for
 * a consumer whose header needs its own interactive content (a ref link, say):
 * projecting that into `[accordionHeader]` would nest a real anchor inside this
 * component's real `<button>`, invalid content model that also gives a click two
 * conflicting targets.
 */
@Component({
  selector: 'fleet-kit-accordion-section',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-accordion-section.html',
  styleUrl: './kit-accordion-section.css',
})
export class KitAccordionSection {
  /** Whether this section currently reads open — driven entirely by the consumer. */
  readonly expanded = input(false);

  /** A stable id unique among a consumer's own sections — this component's own
   * `aria-controls`/`aria-labelledby` pair derives from it, so two sections on the
   * same page never collide. */
  readonly sectionId = input.required<string>();

  /** Emitted with the section's next open state when the header is activated. */
  readonly expandedChange = output<boolean>();

  protected toggle(): void {
    this.expandedChange.emit(!this.expanded());
  }
}

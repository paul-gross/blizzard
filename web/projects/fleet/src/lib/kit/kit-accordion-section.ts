import {
  AccordionContent,
  AccordionPanel,
  AccordionTrigger,
} from '@angular/aria/accordion';
import {
  ChangeDetectionStrategy,
  Component,
  input,
  model,
} from '@angular/core';

/**
 * A single collapsible section — a header button toggling a projected body's visibility,
 * the shared building block behind the node history tab's Transcripts/Artifacts panels and
 * the issue list. It lives inside a {@link KitAccordionGroup}, which owns the keyboard model:
 * Up/Down/Home/End between headers, Enter and Space to toggle.
 *
 * Aria's `ngAccordionTrigger`/`ngAccordionPanel` own `aria-expanded`, `aria-controls`, the
 * `region` role and the open/closed state. The consumer's `[(expanded)]` stays authoritative:
 * it is pushed in, and written back only when a person toggles the header. More than one
 * section reads open at once with nothing here coordinating that.
 *
 * The header is projected (`[accordionHeader]`), not a `label`/`count` input pair —
 * a plain uppercase tag label and a richer title both need to fit through the same
 * slot without a second, parallel API for the richer shape. A second slot,
 * `[accordionAside]`, sits beside the trigger `<button>` rather than inside it — for
 * a consumer whose header needs its own interactive content (a ref link, say):
 * projecting that into `[accordionHeader]` would nest a real anchor inside this
 * component's real `<button>`, invalid content model that also gives a click two
 * conflicting targets.
 *
 * The body stays projected, so a consumer's components are built whether or not the section
 * is open; only their rendering into the panel follows the open state.
 */
@Component({
  selector: 'fleet-kit-accordion-section',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AccordionTrigger, AccordionPanel, AccordionContent],
  templateUrl: './kit-accordion-section.html',
  styleUrl: './kit-accordion-section.css',
})
export class KitAccordionSection {
  /** Whether this section currently reads open; two-way, written back only by a person's toggle. */
  readonly expanded = model(false);

  /** A stable id unique among a consumer's own sections — the trigger's and the panel's
   * ids derive from it, so two sections on the same page never collide. */
  readonly sectionId = input.required<string>();

  /** A disabled header is still reached by the arrow keys but does not toggle. */
  readonly disabled = input(false);
}

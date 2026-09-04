import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';

/**
 * The shell a structurally-rendered artifact sits in: the structured reading, with
 * the artifact's own verbatim content one click away behind a toggle.
 *
 * The verbatim record is not optional chrome. Every structured reading in this
 * directory is produced by a hand-maintained mirror of a Python shape that nothing
 * regenerates (`parse-finding-delta.ts`, `parse-finding-survey.ts`), so the reading
 * can be wrong or incomplete; an operator verifying what the agent actually published
 * needs the bytes, not only this app's interpretation of them. Owning that here — not
 * once per shape — is what keeps the two from drifting on the toggle's wording,
 * placement, or its default.
 *
 * Starts closed: the structured view is the reason these components exist. The raw
 * `<pre>` and the projected body genuinely swap in the DOM rather than one being
 * hidden, so a spec asserting "the structured body is gone" reads the same thing an
 * operator sees.
 *
 * Presentational only, with no knowledge of the shape it wraps — the structured body
 * is projected content and `raw` is a plain input.
 */
@Component({
  selector: 'fleet-chunk-artifact-raw-disclosure',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-artifact-raw-disclosure.html',
  styleUrl: './chunk-artifact-raw-disclosure.css',
})
export class ChunkArtifactRawDisclosure {
  /** The artifact's own verbatim content — what the toggle reveals. */
  readonly raw = input.required<string>();

  /** The root the two handles this component renders derive from (`-raw-toggle`,
   * `-raw`) — supplied by the parent, which already roots its own handles under the
   * mount's testid so two mounts never collide. */
  readonly testid = input.required<string>();

  protected readonly showRaw = signal(false);
}

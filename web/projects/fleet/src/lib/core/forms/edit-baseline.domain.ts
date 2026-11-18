/**
 * What an edit started from: the record's id, the revision the hub reported, and a detached snapshot of its
 * draft, all captured from one read (`bzh:frontend-edit-baseline`).
 *
 * The container captures it when editing starts. A query refresh does not replace it, and the working draft is a
 * separate copy. The revision is sent only as `If-Match`; it is never an editable field or a local counter, and an
 * unversioned operation does not invent one.
 */
export interface EditBaseline<D> {
  readonly id: string;
  /** The iteration read from the hub. */
  readonly revision: number;
  /** A detached snapshot from that same read. */
  readonly draft: D;
}

import { required, schema, validate } from '@angular/forms/signals';

import type { RunDraft } from '../core/run.domain';

/**
 * The frontend's own rules for a run (`decision:ui-toolkit/validate-both`): a scope is required and must be one the
 * dialog offers, and delta applies only to a scope the routine has a baseline for — a full run has no such condition.
 * `offered` and `swept` read the dialog's current scopes, so the rules follow them as they load.
 */
export function runRules(offered: () => ReadonlySet<string>, swept: () => ReadonlySet<string>) {
  return schema<RunDraft>((p) => {
    required(p.scope);
    validate(p.scope, ({ value }) =>
      value() === '' || offered().has(value()) ? null : { kind: 'scope', message: 'Choose one of the offered scopes' },
    );
    validate(p.mode, ({ value, valueOf }) =>
      value() !== 'delta' || swept().has(valueOf(p.scope))
        ? null
        : { kind: 'mode', message: 'Delta needs a baseline — this routine has never swept this scope' },
    );
  });
}

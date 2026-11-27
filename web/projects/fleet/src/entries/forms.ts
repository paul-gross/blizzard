/* The `fleet/forms` entry point: the form submit concern, which reaches `@angular/forms`. */

export type { EditBaseline } from '../lib/core/forms/edit-baseline.domain';
export type { RefusalMap, SaveOutcome } from '../lib/core/forms/outcome';
export { createRefusalState, submitForm } from '../lib/core/forms/submit';

import { expect } from '@playwright/test';
import { tenancy } from './tenancy';

/** The package's `test` and `expect`: the only module a spec imports. */
export const test = tenancy;
export { expect };

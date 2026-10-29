import * as fs from 'node:fs';
import * as path from 'node:path';

const PACKAGED_ROOT = path.join(__dirname, '../../src/blizzard/hub/system_artifacts');

/** The text of a document the product packages as a system artifact, read from the source tree by its name (`garden/finding-format`). */
export function packagedSystemArtifact(name: string): string {
  return fs.readFileSync(path.join(PACKAGED_ROOT, `${name}.md`), 'utf8');
}

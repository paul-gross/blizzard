import { openLookup, type Tone } from 'fleet';

import { lifecycleTone } from '../core/lifecycle-tone';
import type { GraphLifecycle, UnknownGraphValue } from './graph.domain';

const toneByLifecycle = openLookup<Exclude<GraphLifecycle, UnknownGraphValue>, Tone>(
  {
    effective: lifecycleTone(false),
    superseded: 'idle',
    retired: lifecycleTone(true),
  },
  () => 'idle',
);

/**
 * A graph version's `lifecycle` → badge {@link Tone}. `effective` and `retired` come from
 * {@link lifecycleTone}; `superseded`, and a lifecycle this build does not know, reuse `idle`'s dim.
 */
export function graphLifecycleTone(lifecycle: GraphLifecycle): Tone {
  return typeof lifecycle === 'string' ? toneByLifecycle(lifecycle) : 'idle';
}

import { HubEventKind } from './hub-events.domain';
import { translateHubEvent } from './hub-events.translate';

describe('translateHubEvent', () => {
  it('maps known frames to independent facts, retaining open vocabularies', () => {
    const raw = { chunk_id: 'ch_1', cause: 'future-cause', terminal: true, key: 'fact', extra: 42 };
    expect(translateHubEvent('chunk-changed', raw)).toEqual({
      type: HubEventKind.CHUNK_CHANGED,
      data: { chunk_id: 'ch_1', cause: 'future-cause', terminal: true, key: 'fact' },
    });
  });

  it('retains unfamiliar kinds and their original payload; rejects malformed known frames', () => {
    const raw = { future: { value: 42 } };
    expect(translateHubEvent('future-kind', raw)).toEqual({ type: 'future-kind', data: { raw } });
    for (const raw of [null, 'not json', 1, []]) expect(translateHubEvent('chunk-changed', raw)).toBeNull();
  });
});

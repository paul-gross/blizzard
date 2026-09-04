import { parseFindingDelta } from './parse-finding-delta';

describe('parseFindingDelta', () => {
  it('parses a delta carrying all three op kinds', () => {
    const delta = parseFindingDelta(
      JSON.stringify({
        scope: 'runner-daemon',
        revisions: { blizzard: '05eb39ec51ccf5fc3773dca6af414d059fcba1d5' },
        measurement: '225 Python files swept',
        findings: [
          { op: 'add', class: 'wide-seam', locus: 'a.py:1', summary: 'seam too wide', introduced: '38faf3daf', ref: 'F1' },
          { op: 'observed', id: 'fin_01ABC' },
          { op: 'gone', id: 'fin_01DEF', note: 'no longer reproduces' },
        ],
      }),
    );

    expect(delta).toEqual({
      scope: 'runner-daemon',
      revisions: { blizzard: '05eb39ec51ccf5fc3773dca6af414d059fcba1d5' },
      measurement: '225 Python files swept',
      findings: [
        { op: 'add', class: 'wide-seam', locus: 'a.py:1', summary: 'seam too wide', introduced: '38faf3daf', ref: 'F1' },
        { op: 'observed', id: 'fin_01ABC' },
        { op: 'gone', id: 'fin_01DEF', note: 'no longer reproduces' },
      ],
    });
  });

  it('parses a delta with zero findings and no revisions/measurement — defaults filled in', () => {
    const delta = parseFindingDelta(JSON.stringify({ scope: 'runner-daemon', findings: [] }));

    expect(delta).toEqual({ scope: 'runner-daemon', revisions: {}, measurement: null, findings: [] });
  });

  it('fills an add op’s optional introduced/ref with null when the artifact omits them', () => {
    const delta = parseFindingDelta(
      JSON.stringify({
        scope: 's',
        findings: [{ op: 'add', class: 'c', locus: 'l', summary: 'sum' }],
      }),
    );

    expect(delta?.findings).toEqual([{ op: 'add', class: 'c', locus: 'l', summary: 'sum', introduced: null, ref: null }]);
  });

  it('tolerates an unknown top-level field and an unknown field on an op, ignoring both', () => {
    const delta = parseFindingDelta(
      JSON.stringify({
        scope: 's',
        findings: [{ op: 'observed', id: 'fin_1', extra_future_field: true }],
        some_future_top_level_field: 'x',
      }),
    );

    expect(delta).toEqual({ scope: 's', revisions: {}, measurement: null, findings: [{ op: 'observed', id: 'fin_1' }] });
  });

  it('rejects a routine-authored survey document sharing scope/revisions/measurement but carrying no findings list — the shape a sloppy validator gets wrong', () => {
    // This is the actual shape of a survey.survey.N artifact: it shares three of
    // FindingDelta's four keys, but its own convention names `candidates` rather
    // than the discriminated `findings` op list, and never carries `findings` at
    // all. Requiring `findings` to be present (see parse-finding-delta.ts's own
    // doc comment on this one deliberate departure from the Python model's
    // optionality) is what keeps this falling through to raw rather than being
    // structurally rendered as a delta.
    const delta = parseFindingDelta(
      JSON.stringify({
        scope: 'runner-daemon',
        revisions: { blizzard: '05eb39ec5' },
        measurement: '225 Python files swept',
        ground_swept: 'src/blizzard/runner/',
        method: 'Per-rule mechanical detection',
        gates_treated_as_out_of_range: 'tests/test_layering.py',
        swept_clean: 'bzh:domain-core',
        notes_for_reconcile: 'CR-20260829 convergence',
        candidates: [{ ref: 'F1', class: 'wide-seam', locus: 'a.py:1', summary: 'seam too wide' }],
      }),
    );

    expect(delta).toBeNull();
  });

  it('rejects a garden-proposal artifact — a top-level array, not an object', () => {
    expect(parseFindingDelta('[]')).toBeNull();
  });

  it('rejects plain non-JSON text', () => {
    expect(parseFindingDelta('recorded')).toBeNull();
  });

  it('rejects JSON with no scope', () => {
    expect(parseFindingDelta(JSON.stringify({ findings: [] }))).toBeNull();
  });

  it('rejects the whole delta when one findings entry fails its own op check, rather than dropping just that entry', () => {
    const delta = parseFindingDelta(
      JSON.stringify({
        scope: 's',
        findings: [{ op: 'observed', id: 'fin_1' }, { op: 'add', class: 'c' /* missing locus/summary */ }],
      }),
    );

    expect(delta).toBeNull();
  });

  it('rejects an unrecognized op value', () => {
    expect(parseFindingDelta(JSON.stringify({ scope: 's', findings: [{ op: 'reopen', id: 'fin_1' }] }))).toBeNull();
  });

  it('rejects revisions carrying a non-string value', () => {
    expect(parseFindingDelta(JSON.stringify({ scope: 's', findings: [], revisions: { blizzard: 1 } }))).toBeNull();
  });

  it('accepts an explicit null measurement — the Python model allows it', () => {
    const delta = parseFindingDelta(JSON.stringify({ scope: 's', findings: [], measurement: null }));

    expect(delta).toEqual({ scope: 's', revisions: {}, measurement: null, findings: [] });
  });
});

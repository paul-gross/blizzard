import { parseFindingSurvey } from './parse-finding-survey';

const SURVEY = {
  scope: 'runner-daemon',
  revisions: { blizzard: '05eb39ec51ccf5fc3773dca6af414d059fcba1d5' },
  measurement: '225 Python files swept.',
  candidates: [
    {
      ref: 'F1',
      class: 'wide-seam',
      locus: 'src/a.py::IHarnessAdapter',
      summary: 'Declares 15 methods spanning five unrelated jobs.',
      introduced: '38faf3daf',
    },
  ],
};

describe('parseFindingSurvey', () => {
  it('parses a full survey, keeping every candidate field', () => {
    const survey = parseFindingSurvey(JSON.stringify(SURVEY));

    expect(survey?.scope).toBe('runner-daemon');
    expect(survey?.measurement).toBe('225 Python files swept.');
    expect(survey?.revisions).toEqual({ blizzard: '05eb39ec51ccf5fc3773dca6af414d059fcba1d5' });
    expect(survey?.candidates).toEqual([
      {
        ref: 'F1',
        class: 'wide-seam',
        locus: 'src/a.py::IHarnessAdapter',
        summary: 'Declares 15 methods spanning five unrelated jobs.',
        introduced: '38faf3daf',
      },
    ]);
  });

  it('defaults revisions and measurement when the survey omits them', () => {
    const survey = parseFindingSurvey(JSON.stringify({ scope: 's', candidates: [] }));

    expect(survey).toEqual({ scope: 's', revisions: {}, measurement: null, candidates: [] });
  });

  it("defaults a candidate's best-effort ref and introduced when it omits them", () => {
    const survey = parseFindingSurvey(
      JSON.stringify({ scope: 's', candidates: [{ class: 'c', locus: 'l', summary: 'm' }] }),
    );

    expect(survey?.candidates[0]).toEqual({ ref: null, class: 'c', locus: 'l', summary: 'm', introduced: null });
  });

  it('ignores an unknown key rather than failing the whole parse, so a later field addition does not fall back to raw', () => {
    const survey = parseFindingSurvey(
      JSON.stringify({ ...SURVEY, whenSwept: '2026-09-04', candidates: [{ ...SURVEY.candidates[0], severity: 'high' }] }),
    );

    expect(survey?.candidates).toHaveLength(1);
  });

  it('returns null for a delta, which shares the head but carries findings rather than candidates', () => {
    const delta = { scope: 'runner-daemon', revisions: {}, measurement: null, findings: [] };

    expect(parseFindingSurvey(JSON.stringify(delta))).toBeNull();
  });

  it('returns null for malformed JSON, a non-object top level, and a missing scope', () => {
    expect(parseFindingSurvey('not json at all')).toBeNull();
    expect(parseFindingSurvey('[1, 2, 3]')).toBeNull();
    expect(parseFindingSurvey(JSON.stringify({ candidates: [] }))).toBeNull();
  });

  it('fails the whole parse when one candidate is malformed, rather than dropping it silently', () => {
    const raw = JSON.stringify({ scope: 's', candidates: [SURVEY.candidates[0], { class: 'c', locus: 'l' }] });

    expect(parseFindingSurvey(raw)).toBeNull();
  });

  it('returns null when revisions is not a flat string map', () => {
    expect(parseFindingSurvey(JSON.stringify({ scope: 's', candidates: [], revisions: { a: 1 } }))).toBeNull();
  });
});

import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type RequestClientStub, stubAnswerRaceLost, stubError, stubHubClient } from 'fleet/testing';

import { hubChunkKey, hubChunksKey, hubDecisionsKey } from '../../core/query-keys';
import { injectAwaitingHumanService } from './awaiting-human.service';

const notices = { begin: vi.fn(), outcome: vi.fn<(message: string) => void>() };

@Component({ selector: 'app-awaiting-human-service-host', template: '' })
class Host {
  readonly service = injectAwaitingHumanService(notices);
}

function setup(routes: Parameters<typeof stubHubClient>[0]) {
  const stub = stubHubClient(routes);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
  });
  const fixture = TestBed.createComponent(Host);
  return { stub, queryClient, service: fixture.componentInstance.service };
}

const ANSWERED = {
  won: true,
  question_id: 'qn_1',
  answer: 'rest',
  answered_by: 'operator',
  answered_at: '2026-07-13T00:00:00Z',
};

const RESOLVED = {
  decision_id: 'de_1',
  choice: 'approve',
  note: null,
  overrode_red_checks: false,
  resolved_by: 'operator',
  resolved_at: '2026-07-13T00:00:00Z',
};

const keysOf = (spy: { mock: { calls: readonly (readonly unknown[])[] } }) =>
  spy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);

describe('injectAwaitingHumanService', () => {
  let stub: RequestClientStub;
  beforeEach(() => {
    notices.begin.mockReset();
    notices.outcome.mockReset();
  });
  afterEach(() => stub?.restore());

  describe('answer', () => {
    it('clears the notices, sends the trimmed answer, and resolves only after the chunk re-reads', async () => {
      const s = setup({ 'POST /api/questions/{question_id}/answers': () => ANSWERED });
      stub = s.stub;
      let release!: () => void;
      const gate = new Promise<void>((resolve) => (release = resolve));
      const invalidate = vi.spyOn(s.queryClient, 'invalidateQueries').mockImplementation(() => gate);

      let settled = false;
      const pending = s.service.answer('ch_1', 'qn_1', { answer: ' rest ' }).then((o) => {
        settled = true;
        return o;
      });
      expect(notices.begin).toHaveBeenCalledTimes(1);
      await vi.waitFor(() => expect(stub.forRoute('/api/questions/qn_1/answers', 'POST')).toHaveLength(1));
      await new Promise((r) => setTimeout(r, 0));
      expect(settled).toBe(false);
      expect(stub.forRoute('/api/questions/qn_1/answers', 'POST')[0].body).toEqual({ answer: 'rest', answered_by: 'operator' });

      release();
      expect(await pending).toEqual({ ok: true });
      expect(keysOf(invalidate)).toEqual([hubChunkKey('ch_1'), hubChunksKey]);
    });

    it('says a lost race on the outcome notice, naming the winner, and answers ok', async () => {
      const s = setup({
        'POST /api/questions/{question_id}/answers': () =>
          stubAnswerRaceLost({ ...ANSWERED, won: false, answer: 'graphql', answered_by: 'alice' }),
      });
      stub = s.stub;

      expect(await s.service.answer('ch_1', 'qn_1', { answer: 'rest' })).toEqual({ ok: true });
      expect(notices.outcome).toHaveBeenCalledWith('alice answered first: “graphql”');
    });

    it('answers any other refusal on the form, never throwing or retrying', async () => {
      const s = setup({
        'POST /api/questions/{question_id}/answers': () => stubError(404, { detail: 'unknown question qn_1' }),
      });
      stub = s.stub;

      expect(await s.service.answer('ch_1', 'qn_1', { answer: 'rest' })).toEqual({
        ok: false,
        fields: {},
        form: 'unknown question qn_1',
      });
      expect(notices.outcome).not.toHaveBeenCalled();
      expect(stub.forRoute('/api/questions/qn_1/answers', 'POST')).toHaveLength(1);
    });

    it('lands the answer validation path on the answer field', async () => {
      const s = setup({
        'POST /api/questions/{question_id}/answers': () =>
          stubError(422, { detail: [{ loc: ['body', 'answer'], msg: 'too long', type: 'value_error' }] }),
      });
      stub = s.stub;

      expect(await s.service.answer('ch_1', 'qn_1', { answer: 'rest' })).toEqual({
        ok: false,
        fields: { answer: 'too long' },
        form: null,
      });
    });
  });

  describe('resolve', () => {
    it('clears the notices, sends the choice with the trimmed note, and re-reads the chunk, list, and gates', async () => {
      const s = setup({ 'POST /api/decisions/{decision_id}/resolutions': () => RESOLVED });
      stub = s.stub;
      const invalidate = vi.spyOn(s.queryClient, 'invalidateQueries');

      expect(await s.service.resolve('ch_1', 'de_1', 'approve', { note: ' careful ' })).toEqual({ ok: true });
      expect(notices.begin).toHaveBeenCalledTimes(1);
      expect(stub.forRoute('/api/decisions/de_1/resolutions', 'POST')[0].body).toEqual({
        choice: 'approve',
        resolved_by: 'operator',
        note: 'careful',
      });
      expect(keysOf(invalidate)).toEqual([hubChunkKey('ch_1'), hubChunksKey, hubDecisionsKey]);
    });

    it('omits a blank note from the body', async () => {
      const s = setup({ 'POST /api/decisions/{decision_id}/resolutions': () => RESOLVED });
      stub = s.stub;

      await s.service.resolve('ch_1', 'de_1', 'approve', { note: '  ' });
      expect(stub.forRoute('/api/decisions/de_1/resolutions', 'POST')[0].body).toEqual({
        choice: 'approve',
        resolved_by: 'operator',
      });
    });

    it('says a lost race on the outcome notice and answers ok', async () => {
      const s = setup({
        'POST /api/decisions/{decision_id}/resolutions': () =>
          stubError(409, {
            decision_id: 'de_1',
            already_resolved_by: 'alice',
            resolved_choice: 'reject',
            resolved_at: '2026-07-13T00:01:00Z',
            detail: 'decision already resolved',
          }),
      });
      stub = s.stub;

      expect(await s.service.resolve('ch_1', 'de_1', 'approve', { note: '' })).toEqual({ ok: true });
      expect(notices.outcome.mock.calls[0][0]).toMatch(/^reject by alice, /);
    });

    it('answers any other refusal on the form', async () => {
      const s = setup({
        'POST /api/decisions/{decision_id}/resolutions': () =>
          stubError(400, { detail: "choice 'maybe' is not one of approve, reject" }),
      });
      stub = s.stub;

      expect(await s.service.resolve('ch_1', 'de_1', 'maybe', { note: 'n' })).toEqual({
        ok: false,
        fields: {},
        form: "choice 'maybe' is not one of approve, reject",
      });
      expect(notices.outcome).not.toHaveBeenCalled();
    });
  });
});

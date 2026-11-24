import { describe, expect, it } from 'vitest';
import { runnerEscalationView } from 'fleet/testing';

import { fromEscalationView } from './escalation.translate';

describe('fromEscalationView', () => {
  it('carries the escalated node, its commands and its closure', () => {
    const wire = runnerEscalationView({
      cause: 'stuck',
      closure: { closed_at: 't', reason: 'escalated' },
      wrapped_takeover_command: 'bz take L-1',
      harness_id: 'claude_code',
    });
    const escalation = fromEscalationView(wire);
    expect(escalation).toMatchObject({
      lease_id: 'L-1',
      node_id: 'build',
      cause: 'stuck',
      closure: { closed_at: 't', reason: 'escalated' },
      resume_command: 'claude --resume s-1',
      wrapped_takeover_command: 'bz take L-1',
      harness_id: 'claude_code',
    });
  });

  it('reads an escalation with no recorded closure as unclosed', () => {
    expect(fromEscalationView(runnerEscalationView()).closure).toBeNull();
  });
});

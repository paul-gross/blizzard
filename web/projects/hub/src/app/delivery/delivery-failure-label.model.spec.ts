import { deliveryFailureLabel } from './delivery-failure-label.model';

describe('deliveryFailureLabel', () => {
  it.each([
    ['conflict', 'merge conflict'],
    ['inherited-failure', 'existing check failures'],
    ['poll-timeout', 'timed out'],
    ['failure', 'delivery failed'],
    [null, 'delivery failed'],
    ['checks', 'checks'],
    ['master-moved', 'master-moved'],
    ['checks timed out', 'checks timed out'],
  ])('labels structured value %s as %s', (value, label) => {
    expect(deliveryFailureLabel(value)).toBe(label);
  });
});

import { deliveryFailureLabel } from './delivery-failure-label.model';

describe('deliveryFailureLabel', () => {
  it.each([
    ['conflict', 'merge conflict'],
    ['inherited-failure', 'existing check failures'],
    ['poll-timeout', 'timed out'],
    ['failure', 'unknown failure'],
    [null, 'unknown failure'],
    ['checks timed out', 'unknown failure'],
  ])('labels structured value %s as %s', (value, label) => {
    expect(deliveryFailureLabel(value)).toBe(label);
  });
});

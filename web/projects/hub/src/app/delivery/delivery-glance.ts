import { ChangeDetectionStrategy, Component } from '@angular/core';

import { injectDeliveryData } from './delivery-data';
import { DeliveryGlanceView } from './delivery-glance-view';

/**
 * The `/delivery` route on a phone — reads the Delivery list {@link injectDeliveryData} joins and hands it to the
 * {@link DeliveryGlanceView}'s cards.
 */
@Component({
  selector: 'app-delivery-glance',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryGlanceView],
  templateUrl: './delivery-glance.html',
  styleUrl: './delivery-glance.css',
})
export class DeliveryGlance {
  private readonly data = injectDeliveryData();

  protected readonly state = this.data.state;
  protected readonly list = this.data.list;
  protected readonly now = this.data.now;
}

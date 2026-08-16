/**
 * What each sales channel is called, for anything DISPLAYING one.
 *
 * There is a second list of channels in this app — the one `/orders/types/`
 * returns, which says what a till may open an order on right now. This is not a
 * duplicate of it and must not be replaced by it: that list holds the channels
 * currently ENABLED, and a café that switches delivery off still has last
 * month's delivery orders in the list, on the reports, and on reprinted
 * receipts. A label looked up from the enabled set would render those as a raw
 * `DELIVERY`, on exactly the screens somebody reads when reconciling.
 *
 * So: the server decides what is sellable, this decides what things are called.
 * `orderTypes.test.ts` fails if a channel is added to the API and not here.
 */
export const ORDER_TYPE_LABELS: Record<string, string> = {
  DINE_IN: 'صالة',
  TAKE_AWAY: 'تيك أواي',
  DELIVERY: 'توصيل',
  // An order that arrived from outside — a phone call, a delivery app — as
  // opposed to DELIVERY, which is the café's own driver taking out a bill
  // somebody rang for. Separate because they are reckoned separately: an app
  // takes a commission and sets its own menu price.
  EXTERNAL: 'طلب خارجي',
}

/**
 * The channel's name, or the raw code.
 *
 * Falling back to the code rather than to an empty string or a dash: an
 * unrecognised channel means this build is older than the server, and a visible
 * `EXTERNAL` on a receipt is a bug report. A blank is a mystery.
 */
export function orderTypeLabel(value: string | null | undefined): string {
  if (!value) return ''
  return ORDER_TYPE_LABELS[value] ?? value
}

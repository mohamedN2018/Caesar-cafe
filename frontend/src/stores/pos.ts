/**
 * The till, as state.
 *
 * The Desktop client folds an order locally from its own event log so it can
 * keep selling with the network down. The web till cannot make that promise —
 * a browser tab with no connection is not a point of sale — so it does the
 * honest opposite: **every mutation is an event POSTed to the server, and the
 * order the screen draws is the one the server folded.** There is no second
 * implementation of a total here, and therefore no way for the figure on the
 * screen to disagree with the figure on the receipt.
 *
 * What that costs is a round trip per tap. What it buys is that the number the
 * cashier reads aloud is the number the customer is charged, always. On a cafe
 * LAN the round trip is not the slow part of taking an order.
 *
 * Events are still client-minted with a UUID, exactly as the Desktop mints
 * them, because that id is the idempotency key: a tap that times out can be
 * retried without ringing the item twice.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, api } from '@/api/client'

export interface ChannelPrice {
  id: string
  order_type: string
  price: string
}

export interface Variant {
  id: string
  name_ar: string
  price: string
  /** Only the channels that DIFFER from `price`. Absent means "same". */
  channel_prices: ChannelPrice[]
  is_default: boolean
  is_active: boolean
}

/**
 * What a variant costs on a channel.
 *
 * Mirrors `ProductVariant.price_for` on the server, and this is the one place
 * the web is allowed to compute a price — for DISPLAY, so a tile can show 20
 * before a delivery order is rung rather than 15 and a surprise on the bill.
 * The figure that is actually charged is still the server's: `ITEM_ADDED`
 * carries only the variant id, and the fold resolves the channel again.
 */
export function priceFor(variant: Variant, orderType: string): string {
  return variant.channel_prices.find((c) => c.order_type === orderType)?.price ?? variant.price
}

export interface Product {
  id: string
  category: string
  category_name: string
  station: string | null
  /** Where it is MADE. The routing rule, shown on the tile. */
  station_name: string | null
  name_ar: string
  sku: string
  is_sellable: boolean
  is_active: boolean
  sort_order: number
  variants: Variant[]
}

export interface Category {
  id: string
  name_ar: string
  color: string
  sort_order: number
  is_active: boolean
  product_count: number
}

export interface Modifier {
  id: string
  name_ar: string
  price_delta: string
}

export interface ModifierGroup {
  id: string
  name_ar: string
  min_select: number
  max_select: number
  modifiers: Modifier[]
}

export interface OrderItem {
  id: string
  line_id: string
  name_snapshot: string
  unit_price_snapshot: string
  price_override: string | null
  price_override_reason: string
  quantity: string
  discount_percent: string
  line_total: string
  status: string
  note: string
  void_reason: string
  fired_at: string | null
  station_name: string | null
  modifiers: { id: string; name_snapshot: string; price_delta_snapshot: string }[]
}

export interface Order {
  id: string
  local_number: string
  order_type: string
  status: string
  subtotal: string
  discount_total: string
  service_total: string
  tax_total: string
  rounding_adjustment: string
  grand_total: string
  paid_total: string
  balance_due: string
  opened_by_name: string | null
  opened_at: string
  items: OrderItem[]
}

export interface PaymentMethod {
  id: string
  code: string
  name_ar: string
  counts_as_cash: boolean
  opens_drawer: boolean
  is_active: boolean
}

export interface Shift {
  id: string
  opened_by_name: string
  opening_cash: string
  status: string
  opened_at: string
}

/** A client-minted id. The idempotency key for the event it names. */
function newId(): string {
  return crypto.randomUUID()
}

export const usePosStore = defineStore('pos', () => {
  const categories = ref<Category[]>([])
  const products = ref<Product[]>([])
  const modifierGroups = ref<ModifierGroup[]>([])
  const methods = ref<PaymentMethod[]>([])
  const shift = ref<Shift | null>(null)

  const order = ref<Order | null>(null)
  const catalogLoading = ref(true)
  const busy = ref(false)
  const error = ref('')

  const activeItems = computed(
    () => order.value?.items.filter((item) => item.status === 'ACTIVE') ?? [],
  )
  const hasItems = computed(() => activeItems.value.length > 0)
  const unfired = computed(() => activeItems.value.filter((item) => item.fired_at === null))
  const isSettled = computed(() => Number(order.value?.balance_due ?? 0) <= 0)

  /** Products the till can actually sell, indexed by category for the grid. */
  const sellable = computed(() =>
    products.value.filter((p) => p.is_active && p.is_sellable && p.variants.length > 0),
  )

  function productsIn(categoryId: string | null): Product[] {
    const list = categoryId
      ? sellable.value.filter((p) => p.category === categoryId)
      : sellable.value
    return [...list].sort((a, b) => a.sort_order - b.sort_order || a.name_ar.localeCompare(b.name_ar))
  }

  async function loadCatalog(): Promise<void> {
    catalogLoading.value = true
    try {
      // `optional` on the two that a role may legitimately lack. A till whose
      // operator cannot read modifier groups still sells, it just cannot offer
      // extras; one who cannot read payment methods can ring an order for a
      // waiter to settle. Neither is worth a red banner, and the product's rule
      // is that a user is never shown a refusal for something they were never
      // offered — the pay button simply is not there.
      //
      // The catalogue is NOT optional: a till with no menu is not a till, and
      // failing loudly there is the honest answer.
      const [cats, prods, groups, pay] = await Promise.all([
        api.get<Category[]>('/catalog/categories/'),
        api.get<Product[]>('/catalog/products/'),
        api.optional<ModifierGroup[]>('/catalog/modifier-groups/'),
        api.optional<PaymentMethod[]>('/payments/methods/'),
      ])
      categories.value = cats.filter((c) => c.is_active)
      products.value = prods
      modifierGroups.value = groups ?? []
      methods.value = (pay ?? []).filter((m) => m.is_active)
      error.value = ''
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل المنيو.'
    } finally {
      catalogLoading.value = false
    }
  }

  async function loadShift(): Promise<void> {
    shift.value = await api.optional<Shift>('/shifts/current/')
  }

  /** Wraps a mutation so one failure never leaves the panel half-updated. */
  async function run<T>(fn: () => Promise<T>, fallback: string): Promise<T | null> {
    busy.value = true
    try {
      const result = await fn()
      error.value = ''
      return result
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : fallback
      return null
    } finally {
      busy.value = false
    }
  }

  async function openOrder(payload: {
    order_type?: string
    table_session?: string | null
  } = {}): Promise<Order | null> {
    const created = await run(
      () =>
        api.post<Order>('/orders/', {
          // Client-minted, so a retry after a timeout reuses the same order
          // rather than opening a second one on the same table.
          order_id: newId(),
          order_type: payload.order_type ?? 'DINE_IN',
          table_session: payload.table_session ?? null,
        }),
      'تعذّر فتح الطلب.',
    )
    if (created) order.value = created
    return created
  }

  async function loadOrder(id: string): Promise<Order | null> {
    const found = await run(() => api.get<Order>(`/orders/${id}/`), 'تعذّر تحميل الطلب.')
    if (found) order.value = found
    return found
  }

  /**
   * Send events and adopt the server's fold as the truth.
   *
   * `approvalToken` rides along for the events that need a manager standing
   * there — a manual price, a discount above the role's ceiling.
   */
  async function send(
    events: { type: string; payload: Record<string, unknown> }[],
    approvalToken?: string,
  ): Promise<Order | null> {
    const current = order.value
    if (!current) return null

    const body = { events: events.map((e) => ({ id: newId(), ...e })) }
    const headers = approvalToken ? { 'X-Approval-Token': approvalToken } : undefined

    const result = await run(
      () => api.post<{ order: Order }>(`/orders/${current.id}/events/`, body, headers),
      'تعذّر حفظ التعديل.',
    )
    if (result) order.value = result.order
    return result?.order ?? null
  }

  function addItem(variantId: string, quantity = 1, modifiers: string[] = [], note = '') {
    return send([
      {
        type: 'ITEM_ADDED',
        payload: { line_id: newId(), variant_id: variantId, quantity: String(quantity), modifiers, note },
      },
    ])
  }

  function setQuantity(lineId: string, quantity: number) {
    return send([
      { type: 'ITEM_QUANTITY_CHANGED', payload: { line_id: lineId, quantity: String(quantity) } },
    ])
  }

  function voidItem(lineId: string, reason = '') {
    return send([{ type: 'ITEM_VOIDED', payload: { line_id: lineId, reason } }])
  }

  function setNote(lineId: string, note: string) {
    return send([{ type: 'ITEM_NOTE_SET', payload: { line_id: lineId, note } }])
  }

  function applyDiscount(percent: number, lineId: string | null, reason: string, token?: string) {
    return send(
      [
        {
          type: 'DISCOUNT_APPLIED',
          payload: lineId
            ? { line_id: lineId, percent: String(percent), reason }
            : { percent: String(percent), reason },
        },
      ],
      token,
    )
  }

  /** `price = null` clears the override and the line returns to menu price. */
  function overridePrice(lineId: string, price: number | null, reason: string, token?: string) {
    return send(
      [
        {
          type: 'ITEM_PRICE_OVERRIDDEN',
          payload: { line_id: lineId, price: price === null ? null : price.toFixed(2), reason },
        },
      ],
      token,
    )
  }

  function fire() {
    return send([{ type: 'ORDER_FIRED', payload: { fired_at: new Date().toISOString() } }])
  }

  async function pay(methodId: string, amount: number, tendered?: number) {
    const current = order.value
    if (!current) return null

    // Minted once, outside `run`, so a retry of a timed-out call reuses the
    // same key. A fresh key per attempt would make the retry a second charge —
    // which is the exact failure the header exists to prevent.
    const key = newId()

    const done = await run(
      () =>
        api.post<{ order: Order }>(
          '/payments/',
          {
            order: current.id,
            method: methodId,
            amount: amount.toFixed(2),
            tendered: tendered === undefined ? undefined : tendered.toFixed(2),
          },
          { 'Idempotency-Key': key },
        ),
      'تعذّر تسجيل الدفعة.',
    )
    if (done?.order) order.value = done.order
    return done
  }

  function clear() {
    order.value = null
  }

  return {
    categories,
    products,
    modifierGroups,
    methods,
    shift,
    order,
    catalogLoading,
    busy,
    error,
    activeItems,
    hasItems,
    unfired,
    isSettled,
    sellable,
    productsIn,
    loadCatalog,
    loadShift,
    openOrder,
    loadOrder,
    send,
    addItem,
    setQuantity,
    voidItem,
    setNote,
    applyDiscount,
    overridePrice,
    fire,
    pay,
    clear,
  }
})

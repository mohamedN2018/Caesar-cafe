<script setup lang="ts">
/**
 * The till board: menu on one side, the bill on the other.
 *
 * The layout follows how the job is actually done rather than how the data is
 * shaped. In RTL the **bill sits on the left, fixed**, because it is the thing
 * being read aloud and pointed at, and a total that moves as the menu scrolls
 * is a total nobody trusts. The menu scrolls; the bill does not.
 *
 * Three rules the grid obeys:
 *
 *   * **A tap adds the default variant immediately.** No dialog, no
 *     confirmation. The overwhelming majority of taps are "one of that", and
 *     making the common case cost two taps to make the rare case cost one is
 *     the wrong trade at a queue.
 *   * A product with real choices (sizes, extras) opens the sheet instead —
 *     the sheet is earned by ambiguity, not applied uniformly.
 *   * **Targets are 96px, not 40px.** This is used with a thumb, sometimes a
 *     wet one. A mis-tap on a till is a wrong item on a bill.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import ItemSheet from '@/modules/pos/ItemSheet.vue'
import OrderPanel from '@/modules/pos/OrderPanel.vue'
import PaymentSheet from '@/modules/pos/PaymentSheet.vue'
import { type Product, priceFor, usePosStore } from '@/stores/pos'

const pos = usePosStore()

/**
 * The order type is a HEADER control, not a dialog on every sale.
 *
 * It changes a few times an hour — when the phone rings — and asking on every
 * order taxes the ninety percent that are dine-in. It is also chosen BEFORE the
 * order opens, because the server fixes it at open time: the type decides which
 * price a line is rung at, and a bill that changed channel halfway would have
 * two prices for the same water.
 */
const ORDER_TYPES = [
  { value: 'DINE_IN', label: 'صالة' },
  { value: 'TAKE_AWAY', label: 'تيك أواي' },
  { value: 'DELIVERY', label: 'توصيل' },
] as const

const activeCategory = ref<string | null>(null)
const search = ref('')
const sheetProduct = ref<Product | null>(null)
const paying = ref(false)
const orderType = ref<string>('DINE_IN')

/**
 * The table this order belongs to, carried from the floor.
 *
 * The floor screen has been sending `?table=&session=&number=` since it became
 * the till's landing screen, and this board ignored all three — so tapping table
 * 2 and ringing a coffee produced a bill attached to nobody. The flow looked
 * correct and did nothing, which is worse than an obvious gap because it is
 * trusted.
 *
 * Read from the route rather than kept in a store: a reload, or a second device
 * opening the same URL, then lands on the same table instead of a blank order.
 */
const route = useRoute()

const tableId = computed(() => (route.query.table as string) || '')
const tableNumber = computed(() => (route.query.number as string) || '')
/** Empty for a free table — one is opened on the first sale. See `sessionFor`. */
const sessionId = ref<string>((route.query.session as string) || '')

/**
 * How many people sat down, as counted on the floor screen.
 *
 * Defaults to one only when nobody was asked. Hard-coding one here — which is
 * what this did — made a party of four open a session claiming a single guest,
 * so the floor board reported "1 من 4" and the room read emptier than it was.
 */
const guestCount = computed(() => {
  const raw = Number(route.query.guests)
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 1
})

/**
 * The session to hang this order on, opening one if the table is free.
 *
 * Seating and ordering are one gesture at a till: nobody taps "seat this party"
 * and then "take their order". Opening it on the first SALE rather than on the
 * tap matters too — walking over to check a bill should not seat a party that
 * does not exist.
 */
async function sessionFor(): Promise<string | null> {
  if (!tableId.value) return null
  if (sessionId.value) return sessionId.value

  const opened = await api.post<{ id: string }>('/floor/sessions/', {
    table: tableId.value,
    guest_count: guestCount.value,
  })
  sessionId.value = opened.id
  return opened.id
}

/**
 * Locked once the bill has a line on it — see the note on ORDER_TYPES.
 *
 * `?.length` because this is a COMPUTED: an order that arrives without an
 * `items` array makes it throw, and a throwing computed does not fail politely —
 * Vue reports "Unhandled error during component update" and the screen stops
 * updating. Guarding the read costs a character; not guarding it costs the till.
 */
const typeLocked = computed(() => (pos.order?.items?.length ?? 0) > 0)

async function startNew() {
  pos.clear()
  await pos.openOrder({
    order_type: orderType.value,
    table_session: await sessionFor(),
  })
}

function chooseType(value: string) {
  if (typeLocked.value) return
  orderType.value = value
  // No order open yet: the choice simply waits for the first tap. One that is
  // open but empty is re-opened, because changing the type of an empty order is
  // free and refusing would be pedantry the cashier has to work around.
  if (pos.order) startNew()
}

const shown = computed(() => {
  const term = search.value.trim()
  if (term) {
    // Search cuts across categories on purpose: somebody hunting for an item
    // by name does not know or care which tab it lives under.
    return pos.sellable.filter((p) => p.name_ar.includes(term) || p.sku.includes(term))
  }
  return pos.productsIn(activeCategory.value)
})

/** A product needs the sheet only when there is genuinely something to choose. */
function needsChoice(product: Product): boolean {
  return product.variants.filter((v) => v.is_active).length > 1
}

function priceOf(product: Product): string {
  const chosen = product.variants.find((v) => v.is_default) ?? product.variants[0]
  return chosen ? priceFor(chosen, orderType.value) : '0.00'
}

/** True when this item is priced differently on the channel now selected. */
function isChannelPriced(product: Product): boolean {
  const chosen = product.variants.find((v) => v.is_default) ?? product.variants[0]
  return chosen ? priceFor(chosen, orderType.value) !== chosen.price : false
}

async function tap(product: Product) {
  if (!pos.order) {
    await pos.openOrder({ order_type: orderType.value, table_session: await sessionFor() })
  }
  if (!pos.order) return

  if (needsChoice(product)) {
    sheetProduct.value = product
    return
  }
  const variant = product.variants.find((v) => v.is_default) ?? product.variants[0]
  if (variant) await pos.addItem(variant.id)
}

onMounted(async () => {
  await Promise.all([pos.loadCatalog(), pos.loadShift()])
})
</script>

<template>
  <div class="board">
    <!-- Menu -->
    <section class="menu">
      <div class="menu-top">
        <!--
          Which table this bill is for.

          The single most important thing on this screen once it is reached from
          the floor, and it was not on it at all: a cashier ringing items had no
          confirmation the order was attached to the table they tapped. A wrong
          bill is discovered at closing, when nobody can reconstruct it.

          A link back rather than a label, because the other thing somebody wants
          here is the room again.
        -->
        <RouterLink v-if="tableNumber" to="/pos" class="for-table">
          طاولة {{ tableNumber }}
        </RouterLink>

        <div class="types">
          <button
            v-for="option in ORDER_TYPES"
            :key="option.value"
            type="button"
            class="type"
            :class="{ 'is-on': orderType === option.value }"
            :disabled="typeLocked"
            @click="chooseType(option.value)"
          >
            {{ option.label }}
          </button>
          <span v-if="typeLocked" class="locked">النوع يتحدد قبل أول صنف</span>
        </div>

        <button type="button" class="fresh" :disabled="pos.busy" @click="startNew">
          طلب جديد
        </button>
      </div>

      <input
        v-model="search"
        type="search"
        class="search"
        placeholder="ابحث عن صنف…"
        autocomplete="off"
      />

      <div class="tabs">
        <button
          type="button"
          class="tab"
          :class="{ 'is-active': activeCategory === null }"
          @click="activeCategory = null"
        >
          الكل
        </button>
        <button
          v-for="category in pos.categories"
          :key="category.id"
          type="button"
          class="tab"
          :class="{ 'is-active': activeCategory === category.id }"
          @click="activeCategory = category.id"
        >
          <!--
            The category's own colour as a dot, not as the tab's background.
            These hexes are entered by a manager, so letting one paint a whole
            control means the contrast of the label depends on a colour nobody
            checked. A dot carries the identity and the label stays readable.
          -->
          <span
            v-if="category.color"
            class="tab-dot"
            :style="{ background: category.color }"
            aria-hidden="true"
          />
          {{ category.name_ar }}
        </button>
      </div>

      <div class="grid-scroll">
        <!--
          No shift is a STATE, not an error to bury in a banner.

          The server refuses a sale without one and says so clearly —
          `SHIFT_REQUIRED`, "يجب فتح وردية قبل البيع". The till was throwing that
          away: `tap()` called `openOrder`, it failed, `if (!pos.order) return`
          gave up, and the message landed in an alert at the bottom of a scrolling
          menu where nobody looks. So a cashier tapped a product, nothing
          happened, and the till was reported as broken.

          A tap that can only fail should not be offered. This replaces the grid
          rather than sitting above it, because the grid is the thing that does
          not work yet, and it names the one action that fixes it.
        -->
        <div v-if="!pos.catalogLoading && !pos.shift" class="needs-shift">
          <p class="needs-shift-title">لازم تفتح وردية قبل البيع</p>
          <p class="needs-shift-body">
            الوردية هي اللي الفلوس تتحسب عليها عند الإغلاق. بدونها البيعة مش
            بتنتمي لحد ولا فيه رصيد يتراجع.
          </p>
          <RouterLink to="/pos/shift" class="needs-shift-go">افتح وردية</RouterLink>
        </div>

        <UiSkeleton v-else-if="pos.catalogLoading" :rows="6" />

        <p v-else-if="!shown.length" class="empty">
          {{ search ? 'لا يوجد صنف بهذا الاسم.' : 'لا توجد أصناف في هذا القسم.' }}
        </p>

        <div v-else class="grid">
          <button
            v-for="product in shown"
            :key="product.id"
            type="button"
            class="tile"
            :class="{ 'has-photo': product.image }"
            :disabled="pos.busy"
            @click="tap(product)"
          >
            <!--
              The photo is a BACKDROP, blurred and dimmed, not the tile's
              content. A cafe photograph is busy — steam, a wooden counter,
              somebody's hand — and a name laid over a sharp one at this size is
              unreadable, which on a till means a mis-tap and a wrong bill. The
              blur turns it into colour and mood; the name and the price stay
              the only sharp things on the tile.
            -->
            <span
              v-if="product.image"
              class="tile-photo"
              :style="{ backgroundImage: `url(${JSON.stringify(product.image)})` }"
              aria-hidden="true"
            />
            <span class="tile-name">{{ product.name_ar }}</span>
            <span class="tile-foot">
              <!--
                The price on the CHANNEL now selected, marked when it differs
                from the room price. A cashier taking a delivery order needs to
                read 20 off the tile, not 15 and then a surprise on the bill —
                that gap is an argument on the phone.
              -->
              <span
                class="tile-price tabular-nums"
                :class="{ 'is-channel': isChannelPriced(product) }"
              >
                {{ priceOf(product) }}
              </span>
              <!--
                Where it gets MADE, not which tab it sits under. The two are
                different axes on purpose — a caesar salad is filed under food
                and made at the cold bar — and this is the line that tells a
                cashier a ticket is about to print somewhere nobody is standing.
              -->
              <span v-if="product.station_name" class="tile-station">
                {{ product.station_name }}
              </span>
            </span>
            <span v-if="needsChoice(product)" class="tile-hint">أحجام</span>
          </button>
        </div>
      </div>
    </section>

    <!-- The bill -->
    <aside class="bill">
      <UiAlert v-if="pos.error" tone="error" class="m-3">{{ pos.error }}</UiAlert>
      <OrderPanel @pay="paying = true" />
    </aside>

    <ItemSheet
      v-if="sheetProduct"
      :product="sheetProduct"
      :order-type="orderType"
      @close="sheetProduct = null"
    />
    <PaymentSheet v-if="paying" @close="paying = false" />
  </div>
</template>

<style scoped>
.board {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
}

.menu {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 0.75rem;
  gap: 0.65rem;
}

.menu-top {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
  flex-wrap: wrap;
}

/* Prominent on purpose: it is the answer to "whose bill is this". */
.for-table {
  display: inline-flex;
  align-items: center;
  padding: 0.3rem 0.75rem;
  border-radius: 999px;
  background: var(--brand-700);
  color: #fff;
  font-size: 0.9rem;
  font-weight: 600;
}

.types {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.type {
  min-height: 2.75rem;
  padding: 0.5rem 1.1rem;
  border-radius: 0.7rem;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--ink-muted);
  font-weight: 700;
}
.type.is-on {
  background: var(--gold-500);
  border-color: var(--gold-500);
  color: var(--fg-on-gold);
}
.type:disabled {
  opacity: 0.5;
}

.locked {
  font-size: 0.72rem;
  color: var(--ink-faint);
}

.fresh {
  min-height: 2.75rem;
  padding: 0.5rem 1.2rem;
  border-radius: 0.7rem;
  background: var(--brand-700);
  color: var(--fg-on-brand);
  font-weight: 700;
}

.search {
  width: 100%;
  padding: 0.8rem 1rem;
  font-size: 1rem;
  border: 1px solid var(--border-strong);
  border-radius: 0.75rem;
  background: var(--surface);
  color: var(--ink);
}

.tabs {
  flex: 0 0 auto;
  display: flex;
  gap: 0.5rem;
  overflow-x: auto;
  padding-bottom: 0.2rem;
  scrollbar-width: thin;
}

.tab {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.1rem;
  border-radius: 999px;
  font-size: 0.95rem;
  font-weight: 600;
  background: var(--surface);
  color: var(--ink-muted);
  border: 1px solid var(--border);
  white-space: nowrap;
}

.tab-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 999px;
  /* A ring in the tab's own background, so the dot stays distinct on the active
     tab where the surface behind it goes dark. */
  box-shadow: 0 0 0 1px rgb(255 255 255 / 0.35);
}
.tab.is-active {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: var(--fg-on-brand);
}

.grid-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: 0.5rem;
}

.grid {
  display: grid;
  /* Fills whatever width there is rather than a fixed column count: the same
     board runs on a 10" tablet and a 24" till screen. */
  grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
  gap: 0.6rem;
}

.tile {
  position: relative;
  overflow: hidden; /* keeps the blurred backdrop inside the rounded corners */
  min-height: 6rem; /* thumb-sized, sometimes a wet thumb */
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.35rem;
  padding: 0.75rem;
  border-radius: 0.85rem;
  border: 1px solid var(--border);
  background: var(--surface);
  text-align: start;
  transition: transform 0.08s ease, box-shadow 0.12s ease;
}

.tile-photo {
  position: absolute;
  /* Inset NEGATIVE: a blur samples beyond its own edge, and without the bleed
     the border shows a pale halo where there is nothing left to sample. */
  inset: -12px;
  background-size: cover;
  background-position: center;
  filter: blur(7px) saturate(1.1);
  /* Low enough that dark ink still clears contrast over any photograph. A
     cafe picture can be almost white (milk, marble) or almost black (espresso,
     a night shot), so the tile cannot rely on the image being one or the
     other — the wash is what makes the text safe either way. */
  opacity: 0.28;
}

.tile.has-photo {
  border-color: var(--border-strong);
}
/* A scrim under the text only, so the name stays crisp against the busiest
   part of any photo without dulling the whole tile. */
.tile.has-photo::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgb(255 255 255 / 0.72), rgb(255 255 255 / 0.25));
  pointer-events: none;
}
/* Three explicit layers: photo (0), scrim (1), text (2). Without the numbers
   the photo — being a child like any other — lands on top of its own scrim. */
.tile-photo {
  z-index: 0;
}
.tile.has-photo::after {
  z-index: 1;
}
.tile.has-photo > .tile-name,
.tile.has-photo > .tile-foot,
.tile.has-photo > .tile-hint {
  position: relative;
  z-index: 2;
}
.tile:active:not(:disabled) {
  /* A visible press, because on a touch screen there is no hover to confirm
     the tap landed and the alternative is tapping twice. */
  transform: scale(0.97);
}
.tile:hover:not(:disabled) {
  box-shadow: 0 2px 10px rgb(0 0 0 / 0.08);
  border-color: var(--border-strong);
}
.tile:disabled {
  opacity: 0.55;
}

.tile-name {
  font-size: 1rem;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.3;
}

.tile-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.4rem;
  width: 100%;
}

.tile-price {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--brand-700);
}
.tile-price.is-channel {
  /* Gold, plus a dotted underline. The colour alone would be a rule somebody
     has to learn; the underline says "this number has a reason" to a cashier
     who has never been told what gold means. */
  color: var(--gold-600);
  border-bottom: 1px dotted currentColor;
}

.tile-station {
  font-size: 0.68rem;
  color: var(--ink-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tile-hint {
  font-size: 0.7rem;
  color: var(--ink-faint);
}

/* The blocking state: no shift, so no sale. */
.needs-shift {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  min-height: 16rem;
  padding: 2rem 1.5rem;
  text-align: center;
}
.needs-shift-title {
  font-size: 1.2rem;
  font-weight: 800;
  color: var(--ink);
}
.needs-shift-body {
  max-width: 26rem;
  font-size: 0.9rem;
  color: var(--ink-muted);
}
.needs-shift-go {
  margin-top: 0.5rem;
  min-height: 52px;
  display: inline-flex;
  align-items: center;
  padding: 0 2rem;
  border-radius: 0.65rem;
  font-weight: 800;
  color: var(--fg-on-brand);
  background-image: var(--brand-gradient);
  box-shadow: var(--shadow-brand);
}

.empty {
  padding: 2rem;
  text-align: center;
  color: var(--ink-muted);
}

.bill {
  flex: 0 0 22rem;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--surface);
  border-inline-start: 1px solid var(--border);
}

@media (max-width: 60rem) {
  /* Narrow screens stack, bill first — it is what the cashier is reading. */
  .board {
    flex-direction: column;
  }
  .bill {
    flex: 0 0 auto;
    max-height: 45%;
    border-inline-start: none;
    border-block-start: 1px solid var(--border);
  }
}
</style>

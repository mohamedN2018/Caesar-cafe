<script setup lang="ts">
/**
 * The till's shell. Deliberately not the admin layout.
 *
 * An admin screen is read on a laptop by somebody with time. A till is used
 * standing up, one-handed, with a customer waiting and a queue behind them.
 * Sharing a layout between the two means the till inherits a sidebar, a
 * breadcrumb and a 14px font — none of which survive contact with a busy
 * Friday.
 *
 * So this shell commits to a different set of rules:
 *
 *   * **The whole viewport, and no scrolling.** `100dvh` rather than `100vh`,
 *     because on a phone or tablet the browser chrome collapses as you scroll
 *     and `100vh` leaves the pay button under the address bar — exactly the
 *     control you cannot afford to hide.
 *   * **Nothing is more than one tap away.** No nav tree; the header carries
 *     the four things a cashier acts on, and nothing else.
 *   * **The shift is always on screen.** Selling into no shift produces sales
 *     that reconcile against nothing, and the cashier finds out at close —
 *     the one moment it cannot be fixed. So the button is amber until a drawer
 *     is open, and it never scrolls away.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { usePosStore } from '@/stores/pos'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const pos = usePosStore()
const router = useRouter()

const clock = ref(new Date())
let ticking: number | undefined

/** Seconds matter to nobody here; the minute is what a cashier reads. */
const time = computed(() =>
  clock.value.toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' }),
)

const shiftOpen = computed(() => pos.shift !== null)

onMounted(() => {
  ticking = window.setInterval(() => (clock.value = new Date()), 30_000)
})
onUnmounted(() => window.clearInterval(ticking))

async function leave() {
  await router.push('/')
}
</script>

<template>
  <div class="pos-shell">
    <header class="pos-header">
      <div class="flex items-center gap-3">
        <span class="pos-brand">القيصر</span>
        <span class="pos-clock tabular-nums">{{ time }}</span>
      </div>

      <!--
        Tabs land here as the floor, today's-orders and shift screens are built.
        Drawing them now would be three links to nothing, and a control that
        does nothing is worse than a control that is not there yet.
      -->

      <div class="flex items-center gap-2">
        <!--
          Amber, not hidden, when there is no shift. A disabled control tells a
          cashier what they cannot do; a coloured one tells them what to do next.
        -->
        <span class="pos-shift" :class="shiftOpen ? 'is-open' : 'is-needed'">
          {{ shiftOpen ? `وردية · ${pos.shift?.opening_cash}` : 'لا توجد وردية' }}
        </span>

        <span class="pos-user">{{ auth.me?.full_name_ar ?? '—' }}</span>
        <button type="button" class="pos-exit" @click="leave">خروج</button>
      </div>
    </header>

    <main class="pos-body">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.pos-shell {
  /* dvh, not vh: mobile browser chrome collapses on scroll and vh would put
     the pay button underneath the address bar. */
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--surface-sunken);
  overflow: hidden;
}

.pos-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.6rem 1rem;
  background: var(--brand-700);
  color: var(--fg-on-brand);
  box-shadow: 0 1px 0 rgb(0 0 0 / 0.15);
}

.pos-brand {
  font-size: 1.15rem;
  font-weight: 800;
  letter-spacing: 0.02em;
  color: var(--gold-300);
}

.pos-clock {
  font-size: 0.95rem;
  opacity: 0.85;
}

.pos-tab {
  padding: 0.5rem 1rem;
  border-radius: 0.6rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: rgb(255 255 255 / 0.75);
  transition: background 0.12s ease;
}
.pos-tab:hover {
  background: rgb(255 255 255 / 0.1);
}
.pos-tab.is-active {
  background: rgb(255 255 255 / 0.16);
  color: #fff;
}

.pos-shift {
  padding: 0.5rem 0.9rem;
  border-radius: 0.6rem;
  font-size: 0.9rem;
  font-weight: 700;
}
.pos-shift.is-open {
  background: rgb(255 255 255 / 0.14);
  color: #fff;
}
.pos-shift.is-needed {
  background: var(--warning);
  color: #fff;
}

.pos-user {
  font-size: 0.9rem;
  opacity: 0.9;
}

.pos-exit {
  padding: 0.5rem 0.9rem;
  border-radius: 0.6rem;
  background: rgb(0 0 0 / 0.2);
  color: #fff;
  font-size: 0.9rem;
}

.pos-body {
  flex: 1 1 auto;
  min-height: 0; /* lets children scroll instead of stretching the shell */
  display: flex;
}
</style>

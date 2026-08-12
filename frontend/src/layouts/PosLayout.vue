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

import logoSmall from '@/assets/brand/logo-64.png'
import { ARABIC_LATIN_DIGITS } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'
import { usePosStore } from '@/stores/pos'
import { useTerminalStore } from '@/stores/terminal'

const auth = useAuthStore()
const pos = usePosStore()
const terminal = useTerminalStore()
const router = useRouter()

const clock = ref(new Date())
let ticking: number | undefined

/** Seconds matter to nobody here; the minute is what a cashier reads. */
const time = computed(() =>
  clock.value.toLocaleTimeString(ARABIC_LATIN_DIGITS, { hour: '2-digit', minute: '2-digit' }),
)

const shiftOpen = computed(() => pos.shift !== null)

onMounted(() => {
  ticking = window.setInterval(() => (clock.value = new Date()), 30_000)
})
onUnmounted(() => window.clearInterval(ticking))

/**
 * Ends the PERSON's session, not the machine's enrolment.
 *
 * The till stays a till and the next cashier signs in with four digits. Sending
 * them to the admin dashboard instead — which is what this did — drops somebody
 * with `orders.create` and nothing else onto a screen with nothing on it.
 */
async function leave() {
  // `auth.logout()` rather than just dropping the tokens: it revokes the
  // refresh token server-side too, so a cashier who has finished their shift
  // cannot be resumed from a token that survived in a browser somewhere.
  await auth.logout()
  await router.push({ name: 'pos-sign-in' })
}
</script>

<template>
  <div class="pos-shell">
    <header class="pos-header">
      <div class="flex items-center gap-3">
        <img :src="logoSmall" alt="القيصر" class="pos-logo" />
        <!--
          Which till this is. Three terminals in a branch look identical on
          screen, and "which one rang this" is the first question asked when a
          drawer is short.
        -->
        <span v-if="terminal.deviceName" class="pos-where">{{ terminal.deviceName }}</span>
        <span class="pos-clock tabular-nums">{{ time }}</span>
      </div>

      <div class="flex items-center gap-2">
        <RouterLink to="/pos" class="pos-tab" active-class="is-active" exact>نقطة البيع</RouterLink>
        <RouterLink to="/pos/shift" class="pos-tab" active-class="is-active">الوردية</RouterLink>
      </div>

      <div class="flex items-center gap-2">
        <!--
          A LINK now, not a label. It was amber and inert, which told a cashier
          they needed a shift and gave them nowhere to go — the one thing worse
          than not saying it.
        -->
        <RouterLink
          to="/pos/shift"
          class="pos-shift"
          :class="shiftOpen ? 'is-open' : 'is-needed'"
        >
          {{ shiftOpen ? `وردية · ${pos.shift?.opening_cash}` : 'افتح وردية' }}
        </RouterLink>

        <span class="pos-user">{{ auth.me?.full_name_ar ?? '—' }}</span>
        <button type="button" class="pos-exit" @click="leave">خروج</button>
      </div>
    </header>

    <main class="pos-body">
      <!--
        A till never shows a blank body.

        This screen was reported as "nothing appears": the header rendered, the
        area below it was empty, and there was no message, no error and no way
        forward. The user strip beside it read `—`, which is this layout's fallback
        for a missing principal — so the session was gone and the screen said
        nothing about it.

        Whatever the cause, an empty body is never the honest output. If there is
        no person on this terminal, say so and offer the one action that helps.
        Diagnosing a blank screen from a photograph is not something a cafe should
        ever be asked to do.
      -->
      <div v-if="!auth.me" class="pos-lost">
        <p class="pos-lost-title">انتهت جلسة الكاشير</p>
        <p class="pos-lost-body">
          الجهاز ما زال مفعّلاً — يلزم فقط تسجيل الدخول بالرمز أو البطاقة من جديد.
        </p>
        <RouterLink to="/pos/sign-in" class="pos-lost-go">تسجيل الدخول بالرمز</RouterLink>
      </div>

      <RouterView v-else />
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

.pos-logo {
  height: 1.9rem;
  width: auto;
}

.pos-where {
  font-size: 0.82rem;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  background: rgb(255 255 255 / 0.14);
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

/* The state that used to be a blank rectangle. */
.pos-lost {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 2rem;
  text-align: center;
}
.pos-lost-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--ink);
}
.pos-lost-body {
  max-width: 26rem;
  color: var(--ink-muted);
}
.pos-lost-go {
  margin-top: 0.5rem;
  min-height: 52px;
  display: inline-flex;
  align-items: center;
  padding: 0 1.75rem;
  border-radius: 0.6rem;
  font-weight: 700;
  color: var(--fg-on-brand);
  background-image: var(--brand-gradient);
  box-shadow: var(--shadow-brand);
}
</style>

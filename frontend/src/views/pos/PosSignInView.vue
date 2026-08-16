<script setup lang="ts">
/**
 * Getting into the till.
 *
 * Two screens in one, because they are the same question asked of two different
 * people at two different times:
 *
 *   * **Once, by a manager:** what machine is this? A licence key, and the
 *     browser becomes "the till by the door" permanently.
 *   * **Every shift, by a cashier:** who is standing here? Four digits, or a
 *     badge under the scanner.
 *
 * The second is the one that has to be fast, so it is what the screen shows by
 * default; the first only appears on a browser that has never been enrolled.
 *
 * **The keypad is not a text input with a number keyboard.** A till runs on a
 * screen mounted at an angle in a bright room, often touched with the side of a
 * thumb. The OS keyboard is small, slow to appear, and covers the very thing
 * you are typing into. Big fixed keys that never move are faster and mis-tapped
 * far less.
 *
 * **The badge field is invisible and always focused.** A QR scanner is a
 * keyboard: it types the code and presses Enter. Making the cashier tap a
 * "scan" button first would be asking them to tell the computer something it
 * can already see.
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import logoBig from '@/assets/brand/logo-256.png'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiInput from '@/components/ui/UiInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useTerminalStore } from '@/stores/terminal'

const PIN_MAX = 8

const terminal = useTerminalStore()
const auth = useAuthStore()
const router = useRouter()

const pin = ref('')
const badge = ref('')
const scanner = ref<HTMLInputElement | null>(null)

const enrolment = ref({ license_key: '', email: '', device_name: '' })

/**
 * `back` rather than only `clear`.
 *
 * A four-digit PIN with one wrong digit used to mean retyping all four, because
 * the only correction available wiped the lot. At a counter, forty times a day,
 * that is the difference between a mistake costing one tap and costing five.
 */
const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'back', '0', 'enter']

const canSubmit = computed(() => pin.value.length >= 4 && !terminal.busy)

function press(key: string) {
  if (key === 'back') {
    pin.value = pin.value.slice(0, -1)
    keepScannerReady()
    return
  }
  if (key === 'clear') {
    pin.value = ''
    keepScannerReady()
    return
  }
  if (key === 'enter') {
    submitPin()
    return
  }
  if (pin.value.length < PIN_MAX) pin.value += key
  keepScannerReady()
}

/**
 * Put focus back on the invisible badge field.
 *
 * **This is the scan bug.** The docstring at the top of this file says the badge
 * field is "invisible and always focused", and a QR scanner is a keyboard that
 * types into whatever has focus. But focus was set once, in `onMounted` — and the
 * first tap on any pad key moves it to that button. So scanning worked only if
 * the cashier had not touched the pad since the screen appeared, which is almost
 * never: they try the PIN, it is the wrong one, they reach for the badge, and
 * nothing happens.
 *
 * Called after every pad interaction, so the invariant the comment claims is
 * actually maintained.
 */
/**
 * Whether the badge sink currently holds focus.
 *
 * This is the scanner's real state: it is a keyboard, so it reaches the app only
 * while that field is focused. Tracking it lets the screen say so — the
 * alternative is a permanently optimistic "ready" label, which is worse than no
 * label because it is believed.
 */
const scannerLive = ref(false)

function keepScannerReady() {
  scanner.value?.focus()
}

/**
 * One keystroke sink, two credentials.
 *
 * A badge always begins `QSRB1.` (`accounts/badges.py`), so the two are trivially
 * separable and a single field can accept both. That also gives PIN entry from a
 * physical keyboard, which was previously impossible: the pad was the only way in,
 * so a terminal with a keyboard and no touchscreen could not be signed in to at
 * all.
 */
const BADGE_PREFIX = 'QSRB1.'

async function submitScanned() {
  const scanned = badge.value.trim()
  badge.value = ''
  if (!scanned) {
    // Enter on an empty field means the cashier is submitting the PIN they just
    // typed on the pad.
    await submitPin()
    return
  }
  if (scanned.startsWith(BADGE_PREFIX)) {
    await finish(await terminal.signIn({ badge: scanned }))
    return
  }
  if (/^\d{4,8}$/.test(scanned)) {
    pin.value = scanned
    await submitPin()
    return
  }
  // Some other QR in the room — a product barcode, a WiFi card. The server says
  // the same thing about one, and saying it here saves a round trip.
  terminal.error = 'هذا الرمز ليس بطاقة موظف.'
}

async function finish(ok: boolean) {
  if (!ok) {
    pin.value = ''
    return
  }
  // Reload the principal so the till knows who it is holding before it draws
  // anything — the board's buttons are shaped by their permissions.
  await auth.load()
  await router.push('/pos')
}

async function submitPin() {
  if (!canSubmit.value) return
  await finish(await terminal.signIn({ pin: pin.value }))
}

async function submitEnrolment() {
  const { license_key, email, device_name } = enrolment.value
  if (!license_key.trim() || !email.trim() || !device_name.trim()) return
  await terminal.enrol({
    license_key: license_key.trim(),
    email: email.trim(),
    device_name: device_name.trim(),
  })
}

onMounted(() => {
  // Focus follows the scanner so a badge works the moment the screen appears.
  if (terminal.isEnrolled) {
    scanner.value?.focus()
    // The dot reflects real focus, so it has to be seeded — otherwise the screen
    // opens with a working scanner reported as idle.
    scannerLive.value = document.activeElement === scanner.value
  }
})
</script>

<template>
  <div class="screen">
    <div class="panel">
      <header class="brand">
        <img :src="logoBig" alt="" class="mark" aria-hidden="true" />
        <div>
          <p class="brand-name">القيصر</p>
          <p v-if="terminal.isEnrolled" class="brand-where">
            {{ terminal.branchName }} · {{ terminal.deviceName }}
          </p>
          <p v-else class="brand-where">تفعيل جهاز جديد</p>
        </div>
      </header>

      <UiAlert v-if="terminal.error" tone="error">{{ terminal.error }}</UiAlert>

      <!-- Once, by a manager. -->
      <form v-if="!terminal.isEnrolled" class="enrol" @submit.prevent="submitEnrolment">
        <p class="lead">
          هذا المتصفح لم يُفعَّل بعد. أدخل مفتاح ترخيص الفرع مرة واحدة، وبعدها يدخل الكاشير
          برمزه أو ببطاقته فقط.
        </p>
        <UiInput v-model="enrolment.license_key" label="مفتاح الترخيص" ltr required />
        <UiInput v-model="enrolment.email" label="البريد المسجل للترخيص" type="email" ltr required />
        <UiInput
          v-model="enrolment.device_name"
          label="اسم هذا الجهاز"
          hint="اسم يعرفه الناس — «كاشير الباب»، «كاشير الداخل»."
          required
        />
        <button type="submit" class="go" :disabled="terminal.busy">تفعيل الجهاز</button>
      </form>

      <!-- Every shift, by a cashier. -->
      <template v-else>
        <p class="lead">أدخل رمزك أو امسح بطاقتك</p>

        <!--
          Dots, not the digits. A PIN typed at a counter is read over a
          shoulder, and the cashier already knows what they pressed.
        -->
        <div class="dots" :class="{ 'is-empty': !pin.length }">
          <span v-for="index in Math.max(pin.length, 4)" :key="index" class="dot" />
        </div>

        <div class="pad">
          <button
            v-for="key in keys"
            :key="key"
            type="button"
            class="key"
            :class="{
              'is-action': key === 'back',
              'is-go': key === 'enter',
            }"
            :disabled="key === 'enter' && !canSubmit"
            @click="press(key)"
          >
            <template v-if="key === 'back'">مسح خانة</template>
            <template v-else-if="key === 'enter'">دخول</template>
            <template v-else>{{ key }}</template>
          </button>
        </div>

        <label class="scan">
          <UiIcon name="key" size="1rem" />
          <!--
            The scanner's state, shown because the field cannot be.

            A working scanner and a dead one look the same against an invisible
            input. The dot follows focus rather than assuming it: an optimistic
            "ready" that is sometimes false is worse than nothing, because it is
            believed. Tapping restores focus, which is the fix for the only way
            this goes wrong in practice — something else took it.
          -->
          <button
            type="button"
            class="scan-state"
            :class="scannerLive ? 'is-live' : 'is-idle'"
            @click="keepScannerReady"
          >
            <span class="scan-dot" aria-hidden="true" />
            {{ scannerLive ? 'الماسح جاهز — امسح البطاقة' : 'اضغط هنا لتفعيل الماسح' }}
          </button>
          <!--
            Off-screen rather than `type=hidden`: a scanner types into whatever
            has focus, so the field must be focusable and must not be visible.
          -->
          <input
            ref="scanner"
            v-model="badge"
            class="scan-input"
            autocomplete="off"
            aria-label="امسح بطاقة الموظف"
            @keyup.enter="submitScanned"
            @focus="scannerLive = true"
            @blur="((scannerLive = false), keepScannerReady())"
          />
        </label>
      </template>
    </div>
  </div>
</template>

<style scoped>
.screen {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  background: var(--brand-900);
}

.panel {
  width: min(23rem, 100%);
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.5rem;
  border-radius: 1.1rem;
  background: var(--surface);
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.mark {
  height: 2.6rem;
  width: auto;
}
.brand-name {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--ink);
  line-height: 1.2;
}
.brand-where {
  font-size: 0.78rem;
  color: var(--ink-muted);
}

.lead {
  font-size: 0.88rem;
  color: var(--ink-muted);
  text-align: center;
}

.enrol {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.enrol .lead {
  text-align: start;
}

.dots {
  display: flex;
  justify-content: center;
  gap: 0.6rem;
  min-height: 1rem;
}
.dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 999px;
  background: var(--brand-700);
}
.dots.is-empty .dot {
  background: var(--surface-sunken);
}

.pad {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.55rem;
}

.key {
  /* Fixed and large. A till is touched with the side of a thumb in a bright
     room; keys that reflow or shrink are keys that get mis-hit. */
  min-height: 3.6rem;
  border-radius: 0.7rem;
  background: var(--surface-sunken);
  color: var(--ink);
  font-size: 1.35rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  transition: transform 0.06s ease;
}
.key:active:not(:disabled) {
  /* Touch has no hover, so the press has to confirm itself or it gets pressed
     twice — and a doubled digit is a failed sign-in. */
  transform: scale(0.96);
}
.key.is-action {
  font-size: 0.95rem;
  color: var(--ink-muted);
}
.key.is-go {
  background: var(--brand-700);
  color: var(--fg-on-brand);
  font-size: 1rem;
}
.key:disabled {
  opacity: 0.45;
}

.go {
  min-height: 3.2rem;
  border-radius: 0.7rem;
  background: var(--brand-700);
  color: var(--fg-on-brand);
  font-weight: 700;
}
.go:disabled {
  opacity: 0.5;
}

.scan {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  font-size: 0.8rem;
  color: var(--ink-faint);
  cursor: text;
}

.scan-input {
.scan-state {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.85rem;
  color: var(--fg-on-brand-muted, rgba(255, 255, 255, 0.74));
}
.scan-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  flex: none;
  background: currentColor;
}
.scan-state.is-live .scan-dot {
  background: var(--gold-400);
  /* A slow pulse, so "listening" reads as a state rather than a decoration. */
  animation: scan-pulse 1.8s var(--ease-out) infinite;
}
.scan-state.is-idle {
  color: var(--fg-on-brand-faint, rgba(255, 255, 255, 0.62));
  text-decoration: underline;
}

@keyframes scan-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* Respect a stated preference: a pulsing dot is exactly what this setting is
   for, and the colour alone still distinguishes the two states. */
@media (prefers-reduced-motion: reduce) {
  .scan-state.is-live .scan-dot {
    animation: none;
  }
}

  /* Focusable but not visible: a scanner is a keyboard and types into focus. */
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
</style>

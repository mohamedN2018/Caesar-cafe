<script setup lang="ts">
/**
 * The card that gets printed and handed to a person.
 *
 * **Shown exactly once**, in the response that created it. The PIN and the
 * badge are hashed the moment they are stored, so this dialog is the only
 * moment either exists in readable form — which is why it says so, loudly, and
 * why closing it is a deliberate act rather than a click-away.
 *
 * The name is on the card because a drawer of identical QR codes is a drawer
 * nobody can sort. The PIN is on it too: a badge left at home is the normal
 * Tuesday, and a cashier who cannot get in is a till that cannot open.
 *
 * `@media print` strips everything but the card, so "print" produces a card and
 * not a screenshot of an admin panel with a sidebar down one side.
 */
import { onMounted, ref, watch } from 'vue'
import QRCode from 'qrcode'

import UiIcon from '@/components/ui/UiIcon.vue'

const props = defineProps<{
  name: string
  badge: string
  /** Absent when reprinting a badge — the PIN is not reissued alongside it. */
  pin?: string
  branch?: string
}>()
const emit = defineEmits<{ close: [] }>()

const qr = ref('')
const failed = ref(false)

async function draw() {
  try {
    qr.value = await QRCode.toDataURL(props.badge, {
      // High correction: this card lives in an apron pocket and gets creased,
      // and a code that stops scanning once it is bent is a card nobody trusts.
      errorCorrectionLevel: 'H',
      margin: 1,
      width: 320,
      color: { dark: '#2a1a16', light: '#ffffff' },
    })
  } catch {
    // The token is printed below the code as text regardless, so a failed
    // render costs the convenience of scanning, not the credential.
    failed.value = true
  }
}

function print() {
  window.print()
}

onMounted(draw)
watch(() => props.badge, draw)
</script>

<template>
  <!--
    `print-only-target` is read by a global rule in `brand.css`: on print,
    everything in the body that does not contain it is hidden. A scoped style
    cannot do that — the sidebar lives outside this component.
  -->
  <div class="scrim print-only-target" @click.self="emit('close')">
    <div class="wrap" role="dialog" aria-modal="true">
      <div class="warn no-print">
        <UiIcon name="shield" size="1.05rem" />
        <p>
          اطبع البطاقة الآن. الرمز والبطاقة لن يظهرا مرة أخرى — يُخزَّنان مشفّرين، وإعادة
          الإصدار تُلغي البطاقة القديمة.
        </p>
      </div>

      <!-- The card itself. This is the only thing `print` keeps. -->
      <article class="card">
        <header class="card-head">
          <span class="monogram" aria-hidden="true">ق</span>
          <div>
            <p class="cafe">كافيه القيصر</p>
            <p v-if="branch" class="branch">{{ branch }}</p>
          </div>
        </header>

        <p class="who">{{ name }}</p>

        <img v-if="qr" :src="qr" class="qr" :alt="`بطاقة ${name}`" />
        <p v-else-if="failed" class="qr-failed">تعذّر رسم الرمز — استخدم النص أدناه.</p>

        <p v-if="pin" class="pin">
          <span class="pin-label">رمز الدخول</span>
          <strong class="tabular-nums">{{ pin }}</strong>
        </p>

        <!--
          The token in text as well as in the code. A scanner that will not read
          a creased card, or a terminal without one, still has a way in — and it
          is the difference between a card that fails and a shift that stops.
        -->
        <p class="token">{{ badge }}</p>
      </article>

      <div class="actions no-print">
        <button type="button" class="ghost" @click="emit('close')">تم — أغلق</button>
        <button type="button" class="go" @click="print">طباعة</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: rgb(0 0 0 / 0.5);
  overflow-y: auto;
}

.wrap {
  width: min(22rem, 100%);
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.warn {
  display: flex;
  gap: 0.55rem;
  padding: 0.75rem 0.9rem;
  border-radius: 0.7rem;
  background: var(--warning-bg);
  color: var(--warning);
  font-size: 0.8rem;
  line-height: 1.5;
}

.card {
  padding: 1.25rem;
  border-radius: 1rem;
  background: #fff;
  color: var(--ink);
  text-align: center;
  border: 1px solid var(--border);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  text-align: start;
}
.monogram {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.5rem;
  background: var(--brand-700);
  color: var(--gold-300);
  font-weight: 700;
  font-size: 0.9rem;
}
.cafe {
  font-size: 0.85rem;
  font-weight: 700;
  line-height: 1.2;
}
.branch {
  font-size: 0.7rem;
  color: var(--ink-muted);
}

.who {
  margin-top: 0.9rem;
  font-size: 1.25rem;
  font-weight: 700;
}

.qr {
  width: 11rem;
  height: 11rem;
  margin: 0.75rem auto 0;
  display: block;
}
.qr-failed {
  margin-top: 1rem;
  font-size: 0.8rem;
  color: var(--danger);
}

.pin {
  margin-top: 0.5rem;
}
.pin-label {
  display: block;
  font-size: 0.7rem;
  color: var(--ink-muted);
}
.pin strong {
  font-size: 1.6rem;
  font-weight: 700;
  letter-spacing: 0.22em;
}

.token {
  margin-top: 0.75rem;
  font-size: 0.55rem;
  color: var(--ink-faint);
  word-break: break-all;
  direction: ltr;
}

.actions {
  display: flex;
  gap: 0.6rem;
}
.actions button {
  flex: 1 1 auto;
  min-height: 2.9rem;
  border-radius: 0.7rem;
  font-weight: 700;
}
.ghost {
  background: var(--surface-sunken);
  color: var(--ink);
}
.go {
  background: var(--brand-700);
  color: var(--fg-on-brand);
}

@media print {
  /* The global rule in `brand.css` hides everything around this; these are the
     component's own adjustments once it is alone on the page. */
  .scrim {
    background: none;
    padding: 0;
    display: block;
  }
  .wrap {
    width: 100%;
    max-width: 20rem;
  }
  .card {
    border: none;
  }
}
</style>

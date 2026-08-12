<script setup lang="ts">
/**
 * Login, including the MFA step.
 *
 * MFA is mandatory for admin roles (C11), so this screen must also handle the
 * enrolment case — otherwise a policy-mandated second factor is a deadlock:
 * login refuses a token until you enrol, and enrolling needs a token.
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import logoBig from '@/assets/brand/logo-256.png'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiInput from '@/components/ui/UiInput.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const email = ref('')
const password = ref('')
const mfaCode = ref('')

const step = ref<'credentials' | 'mfa'>('credentials')
const error = ref('')
const enrolmentToken = ref('')

const canSubmit = computed(() =>
  step.value === 'credentials'
    ? email.value.includes('@') && password.value.length > 0
    : /^\d{6}$/.test(mfaCode.value),
)

async function submit() {
  error.value = ''
  enrolmentToken.value = ''

  try {
    const outcome = await auth.login(email.value, password.value, mfaCode.value || undefined)

    if (outcome.status === 'ok') {
      router.push('/')
    } else if (outcome.status === 'mfa_required') {
      step.value = 'mfa'
    } else {
      enrolmentToken.value = outcome.enrollmentToken
      error.value =
        'هذا الحساب يتطلب تفعيل التحقق بخطوتين. استخدم رمز التفعيل أدناه من شاشة الإعدادات.'
    }
  } catch (caught) {
    error.value =
      caught instanceof ApiError ? caught.message : 'حدث خطأ غير متوقع. برجاء المحاولة مرة أخرى.'
    if (step.value === 'mfa') mfaCode.value = ''
  }
}
</script>

<template>
  <!--
    The room, not a login form on a grey field.

    Deep burgundy with a gold bloom behind the card — the same two colours the
    cafe is painted in, and the same treatment the dashboard hero and the POS
    header use. This is the first thing anybody ever sees of the product, and a
    centred box on `--surface-sunken` said nothing about whose product it is.
  -->
  <div class="login-screen flex min-h-screen items-center justify-center px-4 py-10">
    <div class="login-panel w-full max-w-md">
      <div class="mb-8 text-center">
        <!--
          The real mark. This was a coffee emoji at 48px — the first thing
          anyone saw of the product, rendered in whatever font the machine
          happened to have, in colours belonging to no part of the brand.
        -->
        <img :src="logoBig" alt="" class="login-mark mx-auto h-20 w-auto" aria-hidden="true" />
        <h1 class="login-title mt-4 text-3xl font-bold">القيصر</h1>
        <p class="login-subtitle mt-1 text-sm">نظام الإدارة</p>
      </div>

      <form
        class="space-y-5 rounded-xl border border-line bg-surface p-6 shadow-xl"
        @submit.prevent="submit"
      >
        <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

        <template v-if="step === 'credentials'">
          <UiInput
            v-model="email"
            label="البريد الإلكتروني"
            type="email"
            placeholder="owner@example.com"
            ltr
            required
            autocomplete="username"
          />
          <UiInput
            v-model="password"
            label="كلمة المرور"
            type="password"
            ltr
            required
            autocomplete="current-password"
          />
        </template>

        <template v-else>
          <p class="text-sm text-ink-muted">
            أدخل الرمز المكوّن من ٦ أرقام من تطبيق المصادقة.
          </p>
          <UiInput
            v-model="mfaCode"
            label="رمز التحقق"
            placeholder="000000"
            ltr
            required
            autocomplete="one-time-code"
          />
          <button
            type="button"
            class="text-sm text-brand-700 hover:underline"
            @click="((step = 'credentials'), (mfaCode = ''), (error = ''))"
          >
            رجوع
          </button>
        </template>

        <UiButton type="submit" size="lg" block :loading="auth.loading" :disabled="!canSubmit">
          {{ step === 'credentials' ? 'تسجيل الدخول' : 'تأكيد' }}
        </UiButton>
      </form>

      <p class="login-footnote mt-6 text-center text-xs">
        الاتصال مؤمَّن. لا تشارك بيانات الدخول مع أحد.
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-screen {
  position: relative;
  overflow: hidden;
  background-image: var(--brand-gradient);
}

/*
   A gold bloom behind the card, off-centre.

   Off-centre on purpose: a glow centred exactly behind a centred card reads as
   a rendering artefact — a halo — while one offset above and to the trailing
   side reads as light coming from somewhere in the room.
*/
.login-screen::before {
  content: '';
  position: absolute;
  inset-block-start: -20%;
  inset-inline-end: -15%;
  width: 40rem;
  height: 40rem;
  background: radial-gradient(circle, rgba(201, 162, 39, 0.22) 0%, transparent 62%);
  pointer-events: none;
}
.login-screen::after {
  content: '';
  position: absolute;
  inset-block-end: -25%;
  inset-inline-start: -20%;
  width: 34rem;
  height: 34rem;
  background: radial-gradient(circle, rgba(0, 0, 0, 0.22) 0%, transparent 60%);
  pointer-events: none;
}

.login-panel {
  position: relative;
}

.login-title {
  color: var(--fg-on-brand);
}
.login-subtitle {
  color: var(--fg-on-brand-muted);
}
.login-footnote {
  color: var(--fg-on-brand-faint);
}

/* The mark sits on burgundy now, so it needs a shadow to have an edge. Without
   one the darker parts of the artwork merge into the background. */
.login-mark {
  filter: drop-shadow(0 6px 14px rgba(0, 0, 0, 0.35));
}
</style>

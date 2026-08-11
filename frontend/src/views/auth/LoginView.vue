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
  <div class="flex min-h-screen items-center justify-center bg-surface-sunken px-4">
    <div class="w-full max-w-md">
      <div class="mb-8 text-center">
        <!--
          The real mark. This was a coffee emoji at 48px — the first thing
          anyone saw of the product, rendered in whatever font the machine
          happened to have, in colours belonging to no part of the brand.
        -->
        <img :src="logoBig" alt="" class="mx-auto h-16 w-auto" aria-hidden="true" />
        <h1 class="mt-3 text-2xl font-bold text-ink">القيصر</h1>
        <p class="mt-1 text-sm text-ink-muted">نظام الإدارة</p>
      </div>

      <form
        class="space-y-5 rounded-xl border border-line bg-surface p-6 shadow-sm"
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

      <p class="mt-6 text-center text-xs text-ink-faint">
        الاتصال مؤمَّن. لا تشارك بيانات الدخول مع أحد.
      </p>
    </div>
  </div>
</template>

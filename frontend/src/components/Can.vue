<script setup lang="ts">
/**
 * Renders its contents only for someone who holds the permission.
 *
 * The rule this enforces: **a user is never shown a refusal for something they
 * were never offered.** A disabled button they cannot explain, or a red
 * "ليس لديك صلاحية: inventory.view" after a page loaded itself, both tell
 * somebody off for a request the interface made on their behalf. The honest
 * interface simply does not have that part.
 *
 * This is presentation only. The server re-checks every request, and §62 is
 * explicit that the client is never trusted for authorization — hiding a
 * section makes the screen truthful, not the system safe.
 *
 *     <Can permission="inventory.view">
 *       <StockStrip />
 *     </Can>
 *
 * `any` for either-of; `all` for every-of. Passing none renders nothing, which
 * is the safe direction for a typo.
 */
import { computed } from 'vue'

import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  permission?: string
  any?: string[]
  all?: string[]
}>()

const auth = useAuthStore()

const allowed = computed(() => {
  if (props.permission) return auth.can(props.permission)
  if (props.any?.length) return props.any.some((code) => auth.can(code))
  if (props.all?.length) return props.all.every((code) => auth.can(code))
  return false
})
</script>

<template>
  <slot v-if="allowed" />
  <!-- Deliberately no fallback slot. An "you may not see this" placeholder is
       the message this component exists to remove; a section somebody cannot
       use should leave no trace, so the screen reads as complete. -->
</template>

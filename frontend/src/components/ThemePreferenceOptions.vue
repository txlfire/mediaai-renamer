<script setup lang="ts">
import { Check, Close } from "@element-plus/icons-vue";
import { nextTick, ref } from "vue";
import type { ThemePalette } from "../config/themeRegistry";
import { zhCnMessages as messages } from "../locales/zh-CN";
import type { ResolvedTheme, ThemeMode } from "../stores/app";

defineProps<{
  selected: string;
  mode: ThemeMode;
  scheme: ResolvedTheme;
  heading?: boolean;
  options: readonly { value: string; palette: ThemePalette; label: string }[];
}>();
const emit = defineEmits<{
  select: [value: string];
  mode: [value: ThemeMode];
  dismiss: [event?: KeyboardEvent];
}>();
const text = messages.app.theme;
const body = ref<HTMLElement>();

function revealItem(element: HTMLElement) {
  const item = element.closest<HTMLElement>(".theme-preference-option");
  const list = item?.closest<HTMLElement>(".theme-preference-options");
  if (!item || !list) return;
  const rect = item.getBoundingClientRect();
  const bounds = list.getBoundingClientRect();
  // 只滚列表，避免scrollIntoView连带滚动页面或弹出层的祖先。
  if (rect.top < bounds.top + 3) list.scrollTop += rect.top - bounds.top - 3;
  else if (rect.bottom > bounds.bottom - 3) list.scrollTop += rect.bottom - bounds.bottom + 3;
}

async function focusSelected() {
  await nextTick();
  const input = body.value?.querySelector<HTMLInputElement>('.theme-preference-options input[type="radio"]:checked');
  input?.focus({ preventScroll: true });
  if (input) revealItem(input);
}

function selectTheme(value: unknown) {
  if (typeof value === "string") emit("select", value);
}

function selectMode(value: unknown) {
  if (value === "system" || value === "light" || value === "dark") emit("mode", value);
}

function reveal(event: FocusEvent) {
  revealItem(event.target as HTMLElement);
}

defineExpose({ focusSelected });
</script>

<template>
  <section ref="body" class="theme-preferences" :aria-label="text.settings" @keydown.esc="emit('dismiss', $event)">
    <header v-if="heading" class="theme-preference-heading">
      <strong>{{ text.settings }}</strong>
      <el-button text :icon="Close" :aria-label="text.close" @click="emit('dismiss')" />
    </header>
    <fieldset>
      <legend>{{ text.scheme }}</legend>
      <el-radio-group :model-value="mode" :aria-label="text.scheme" @change="selectMode">
        <el-radio-button value="system">{{ text.system }}</el-radio-button>
        <el-radio-button value="light">{{ text.light }}</el-radio-button>
        <el-radio-button value="dark">{{ text.dark }}</el-radio-button>
      </el-radio-group>
    </fieldset>
    <div class="theme-preference-field">
      <div class="theme-preference-label">{{ text.palette }}</div>
      <el-radio-group :model-value="selected" :aria-label="text.palette" class="theme-preference-options" @change="selectTheme" @focusin="reveal">
        <el-radio v-for="item in options" :key="item.value" :value="item.value" class="theme-preference-option">
          <span class="theme-preview" :data-preview-palette="item.palette" :data-preview-scheme="scheme" aria-hidden="true">
            <i class="theme-preview-sidebar"></i>
            <i class="theme-preview-sheet"><i class="theme-preview-header"></i><i class="theme-preview-lines"></i><i class="theme-preview-command"></i></i>
          </span>
          <span class="theme-preference-caption"><span>{{ item.label }}</span><el-icon v-if="selected === item.value"><Check /></el-icon></span>
        </el-radio>
      </el-radio-group>
    </div>
  </section>
</template>

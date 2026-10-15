<script setup lang="ts">
import { Brush } from "@element-plus/icons-vue";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { isThemePalette, themeDefinitions } from "../config/themeRegistry";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useAppStore } from "../stores/app";
import ThemePreferenceOptions from "./ThemePreferenceOptions.vue";

const appStore = useAppStore();
const text = messages.app.theme;
const open = ref(false);
const body = ref<{ focusSelected: () => Promise<void> }>();
const trigger = ref<{ $el: HTMLButtonElement }>();
const height = ref(typeof window === "undefined" ? 768 : window.innerHeight);
const compact = computed(() => height.value < 400);
const options = themeDefinitions.map(theme => ({ value: theme.id, palette: theme.id, label: text[theme.labelKey] }));
const description = computed(() => {
  const current = options.find(option => option.value === appStore.themePalette)!;
  const mode = appStore.themeMode === "system" ? text.system : appStore.resolvedTheme === "dark" ? text.dark : text.light;
  return `${text.settings}：${current.label} · ${mode}`;
});
const popperOptions = {
  strategy: "fixed" as const,
  modifiers: [
    { name: "preventOverflow", options: { padding: 12, tether: false, altAxis: true } },
    { name: "flip", options: { fallbackPlacements: ["left-end", "top-end", "bottom-end"], padding: 12 } },
  ],
};

function selectTheme(value: string) {
  if (isThemePalette(value)) appStore.setThemePalette(value);
}

async function focusSelected() {
  await nextTick();
  await body.value?.focusSelected();
}

function returnFocus() {
  void nextTick(() => trigger.value?.$el.focus({ preventScroll: true }));
}

function dismiss(event?: KeyboardEvent) {
  if (!open.value) return;
  event?.stopPropagation();
  open.value = false;
  returnFocus();
}

function resize() { height.value = window.innerHeight; }
watch(open, value => { if (value) void focusSelected(); });
// 跨布局时关闭旧浮层，避免销毁Popover后遗留焦点或两个同时打开的面板。
watch(compact, () => { if (open.value) { open.value = false; returnFocus(); } });
onMounted(() => window.addEventListener("resize", resize));
onUnmounted(() => window.removeEventListener("resize", resize));
</script>

<template>
  <el-popover
    v-if="!compact"
    v-model:visible="open"
    placement="right-end"
    trigger="click"
    role="dialog"
    :aria-label="text.settings"
    :width="384"
    popper-class="theme-preference-popover"
    :popper-options="popperOptions"
    :show-arrow="false"
    :trigger-keys="[]"
    @after-enter="focusSelected"
  >
    <template #reference>
      <el-button ref="trigger" class="theme-toggle" circle :aria-label="text.settings" :title="description" :aria-description="description">
        <el-icon><Brush /></el-icon>
      </el-button>
    </template>
    <ThemePreferenceOptions
      ref="body" :selected="appStore.themePalette" :options="options" :mode="appStore.themeMode" :scheme="appStore.resolvedTheme" heading
      @select="selectTheme" @mode="appStore.setThemeMode" @dismiss="dismiss"
    />
  </el-popover>
  <template v-else>
    <el-button ref="trigger" class="theme-toggle" circle :aria-label="text.settings" :title="description" :aria-description="description" :aria-expanded="open" aria-haspopup="dialog" @click="open = !open">
      <el-icon><Brush /></el-icon>
    </el-button>
    <el-dialog
      v-model="open" :title="text.settings" class="theme-preference-dialog" width="min(408px, calc(100vw - 24px))" align-center
      :close-on-press-escape="false" @opened="focusSelected" @keydown.esc="dismiss"
    >
      <ThemePreferenceOptions
        ref="body" :selected="appStore.themePalette" :options="options" :mode="appStore.themeMode" :scheme="appStore.resolvedTheme"
        @select="selectTheme" @mode="appStore.setThemeMode" @dismiss="dismiss"
      />
    </el-dialog>
  </template>
</template>

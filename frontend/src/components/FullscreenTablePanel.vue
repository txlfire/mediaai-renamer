<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { zhCnMessages as messages } from "../locales/zh-CN";

const props = withDefaults(defineProps<{
  title: string;
  variant?: "default" | "tab-header";
}>(), {
  variant: "default",
});

// 模板的Teleport延后解析工作区，兼容父工作区在同一轮挂载中创建。
const isFullscreen = ref(false);
const panelRef = ref<HTMLElement | null>(null);
const toggleRef = ref<{ $el: HTMLButtonElement } | null>(null);
const fullscreenIcon = "\u26f6";
const exitFullscreenIcon = "\u2922";
let resizeObserver: ResizeObserver | null = null;
let resizeFrame = 0;

function requestTableRelayout() {
  if (resizeFrame) {
    cancelAnimationFrame(resizeFrame);
  }
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0;
    window.dispatchEvent(new Event("resize"));
  });
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value;
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape" || !isFullscreen.value || event.defaultPrevented) return;
  // 内部弹窗、菜单与选择器先处理ESC，避免同一次按键连带退出全屏。
  if (event.target instanceof Element && event.target.closest('[role="dialog"], [role="menu"], [role="listbox"], .el-popper, [aria-expanded="true"]')) return;
  isFullscreen.value = false;
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
  if (panelRef.value) {
    resizeObserver = new ResizeObserver(() => requestTableRelayout());
    resizeObserver.observe(panelRef.value);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleKeydown);
  resizeObserver?.disconnect();
  if (resizeFrame) {
    cancelAnimationFrame(resizeFrame);
  }
});

watch(isFullscreen, async () => {
  await nextTick();
  // Teleport搬移节点会使浏览器丢失焦点，返回同一开关而不是页面body。
  toggleRef.value?.$el.focus({ preventScroll: true });
  requestTableRelayout();
});
</script>

<template>
  <Teleport defer to=".app-workbench" :disabled="!isFullscreen">
    <div
      ref="panelRef"
      class="fullscreen-table-panel"
      :class="{
        'is-table-fullscreen': isFullscreen,
        'is-tab-header-fullscreen-panel': props.variant === 'tab-header',
      }"
    >
      <div class="fullscreen-table-toolbar" :class="{ 'is-titleless': !title }">
        <span v-if="title">{{ title }}</span>
        <el-tooltip :trigger-keys="[]" :content="isFullscreen ? messages.common.exitFullscreen : messages.common.fullscreen" placement="top">
          <el-button
            ref="toggleRef"
            class="fullscreen-icon-button"
            :aria-label="isFullscreen ? messages.common.exitFullscreen : messages.common.fullscreen"
            @click="toggleFullscreen"
          >
            {{ isFullscreen ? exitFullscreenIcon : fullscreenIcon }}
          </el-button>
        </el-tooltip>
      </div>
      <slot />
    </div>
  </Teleport>
</template>

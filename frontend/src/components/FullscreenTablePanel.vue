<script setup lang="ts">
import { FullScreen, Close } from "@element-plus/icons-vue";
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { zhCnMessages as messages } from "../locales/zh-CN";

defineProps<{
  title: string;
}>();

const isFullscreen = ref(false);
const panelRef = ref<HTMLElement | null>(null);
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
  if (event.key === "Escape") {
    isFullscreen.value = false;
  }
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
  requestTableRelayout();
});
</script>

<template>
  <Teleport to=".app-workbench" :disabled="!isFullscreen">
    <div ref="panelRef" class="fullscreen-table-panel" :class="{ 'is-table-fullscreen': isFullscreen }">
      <div class="fullscreen-table-toolbar">
        <span>{{ title }}</span>
        <el-button :icon="isFullscreen ? Close : FullScreen" @click="toggleFullscreen">
          {{ isFullscreen ? messages.common.exitFullscreen : messages.common.fullscreen }}
        </el-button>
      </div>
      <slot />
    </div>
  </Teleport>
</template>

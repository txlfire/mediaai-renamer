<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { useInterfaceLanguage } from "../locales/interfaceLanguage";

const { messages } = useInterfaceLanguage();

const props = withDefaults(defineProps<{
  title?: string;
  variant?: "default" | "tab-header" | "embedded";
  actionsHost?: HTMLElement | null;
}>(), {
  variant: "default",
});

// 独立宿主保留路由相邻节点，避免旧页面卸载时一并删除新页面的插入定位节点。
const workbenchHost = ".workbench-fullscreen-host";
const isFullscreen = ref(false);
const fullscreenTarget = ref<HTMLElement | null>(null);
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
  if (!isFullscreen.value) {
    fullscreenTarget.value = panelRef.value?.closest<HTMLElement>(".el-dialog, .el-drawer") ?? null;
  }
  isFullscreen.value = !isFullscreen.value;
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape" || !isFullscreen.value || event.defaultPrevented) return;
  const panel = panelRef.value;
  if (!panel?.getClientRects().length) return;
  if (event.target instanceof Element
    && event.target.closest('[role="menu"], [role="listbox"], .el-popper, [aria-expanded="true"]')) return;

  // Element Plus 在 document 的冒泡阶段处理 Esc；只拦截本面板所在的最上层。
  let topModal: HTMLElement | undefined;
  let highestZ = -Infinity;
  for (const modal of document.querySelectorAll<HTMLElement>('.el-overlay [aria-modal="true"]')) {
    if (!modal.getClientRects().length) continue;
    const overlay = modal.closest<HTMLElement>(".el-overlay")!;
    const zIndex = Number.parseInt(getComputedStyle(overlay).zIndex, 10) || 0;
    if (zIndex >= highestZ) {
      topModal = modal;
      highestZ = zIndex;
    }
  }
  if (topModal ? !topModal.contains(panel) : fullscreenTarget.value !== null) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  isFullscreen.value = false;
}

onMounted(() => {
  document.addEventListener("keydown", handleKeydown, true);
  if (panelRef.value && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => requestTableRelayout());
    resizeObserver.observe(panelRef.value);
  }
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", handleKeydown, true);
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
  <Teleport defer :to="fullscreenTarget || workbenchHost" :disabled="!isFullscreen">
    <div
      ref="panelRef"
      class="fullscreen-table-panel"
      :class="{
        'is-table-fullscreen': isFullscreen,
        'is-modal-table-fullscreen': isFullscreen && fullscreenTarget,
        'is-tab-header-fullscreen-panel': props.variant === 'tab-header',
        'is-embedded-fullscreen-panel': props.variant === 'embedded',
        'is-external-toolbar': actionsHost && !isFullscreen,
      }"
    >
      <div v-show="!actionsHost || isFullscreen" class="fullscreen-table-toolbar"
        :class="{ 'is-titleless': !title, 'is-compact-toolbar': props.variant === 'tab-header' }">
        <h2 v-if="title" class="fullscreen-table-title">{{ title }}</h2>
        <div v-if="$slots.actions" class="fullscreen-table-actions"><slot name="actions" /></div>
        <Teleport defer :to="actionsHost || workbenchHost" :disabled="!actionsHost || isFullscreen">
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
        </Teleport>
      </div>
      <slot />
    </div>
  </Teleport>
</template>

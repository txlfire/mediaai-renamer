<script setup lang="ts">
import { CopyDocument, Download, Refresh } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import {
  exportOperationLogs,
  fetchOperationLogs,
  type OperationLogItem,
} from "../api/client";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import { formatDateTime } from "../utils/displayFormat";

const MAX_RENDERED_LOGS = 1000;
const POLL_INTERVAL_MS = 1500;
const HIDDEN_POLL_INTERVAL_MS = 6000;

const props = defineProps<{
  visible: boolean;
  taskType: string | null;
  taskId: number | null;
  title?: string;
}>();

const emit = defineEmits<{
  (event: "update:visible", value: boolean): void;
}>();

const drawerVisible = computed({
  get: () => props.visible,
  set: (value) => emit("update:visible", value),
});
const logs = ref<OperationLogItem[]>([]);
const latestId = ref(0);
const total = ref(0);
const loading = ref(false);
const autoRefresh = ref(true);
const levelFilter = ref("all");
const statusMessage = ref("");
const foldedCount = ref(0);
const pollTimer = ref<number | null>(null);

const pageText = messages.operationLogs;

const drawerTitle = computed(() => props.title || pageText.title);
const canQuery = computed(() => Boolean(props.taskType && props.taskId));
const visibleLogs = computed(() => {
  if (levelFilter.value === "all") {
    return logs.value;
  }
  return logs.value.filter((item) => item.level === levelFilter.value);
});
const emptyDescription = computed(() => statusMessage.value || pageText.empty);

function clearTimer() {
  if (pollTimer.value !== null) {
    window.clearTimeout(pollTimer.value);
    pollTimer.value = null;
  }
}

function trimLogs() {
  if (logs.value.length <= MAX_RENDERED_LOGS) {
    return;
  }
  const removeCount = logs.value.length - MAX_RENDERED_LOGS;
  logs.value = logs.value.slice(removeCount);
  foldedCount.value += removeCount;
}

function levelLabel(level: string) {
  return pageText.levels[level as keyof typeof pageText.levels] || level;
}

function levelTagType(level: string) {
  if (level === "success") {
    return "success";
  }
  if (level === "warning") {
    return "warning";
  }
  if (level === "error") {
    return "danger";
  }
  return "info";
}

async function loadLogs({ reset = false, silent = false } = {}) {
  if (!canQuery.value || loading.value) {
    return;
  }
  if (reset) {
    logs.value = [];
    latestId.value = 0;
    total.value = 0;
    foldedCount.value = 0;
    statusMessage.value = "";
  }
  loading.value = !silent;
  try {
    const page = await fetchOperationLogs({
      task_type: props.taskType as string,
      task_id: props.taskId as number,
      after_id: reset ? 0 : latestId.value,
      limit: 100,
    });
    if (reset) {
      logs.value = page.items;
    } else if (page.items.length) {
      const existingIds = new Set(logs.value.map((item) => item.id));
      logs.value = logs.value.concat(page.items.filter((item) => !existingIds.has(item.id)));
    }
    latestId.value = page.latest_id;
    total.value = page.total;
    statusMessage.value = page.message || "";
    trimLogs();
    if (!page.running && !page.items.length && latestId.value > 0) {
      autoRefresh.value = false;
    }
    if (page.cleared) {
      autoRefresh.value = false;
    }
  } catch {
    statusMessage.value = pageText.loadFailed;
  } finally {
    loading.value = false;
  }
}

function schedulePoll() {
  clearTimer();
  if (!drawerVisible.value || !autoRefresh.value || !canQuery.value) {
    return;
  }
  const interval = document.visibilityState === "hidden" ? HIDDEN_POLL_INTERVAL_MS : POLL_INTERVAL_MS;
  pollTimer.value = window.setTimeout(async () => {
    await loadLogs({ silent: true });
    schedulePoll();
  }, interval);
}

async function refreshLogs() {
  await loadLogs({ reset: true });
  schedulePoll();
}

function formatLogText(item: OperationLogItem) {
  const progress =
    item.progress_current !== null
      ? ` ${item.progress_current}${item.progress_total !== null ? `/${item.progress_total}` : ""}`
      : "";
  return `[${formatDateTime(item.created_at)}] [${levelLabel(item.level)}] [${item.stage}]${progress} ${item.message}`;
}

async function copyLogs() {
  const text = visibleLogs.value.map(formatLogText).join("\n");
  if (!text) {
    ElMessage.warning(pageText.empty);
    return;
  }
  await navigator.clipboard.writeText(text);
  ElMessage.success(pageText.copySuccess);
}

async function downloadLogs() {
  if (!canQuery.value) {
    return;
  }
  const text = await exportOperationLogs({
    task_type: props.taskType as string,
    task_id: props.taskId as number,
  });
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `operation-log-${props.taskType}-${props.taskId}.txt`;
  link.click();
  URL.revokeObjectURL(url);
}

watch(
  () => [props.visible, props.taskType, props.taskId] as const,
  async ([visible]) => {
    clearTimer();
    if (!visible || !canQuery.value) {
      return;
    }
    autoRefresh.value = true;
    await loadLogs({ reset: true });
    schedulePoll();
  },
);

watch(autoRefresh, () => {
  schedulePoll();
});

document.addEventListener("visibilitychange", schedulePoll);
onBeforeUnmount(() => {
  clearTimer();
  document.removeEventListener("visibilitychange", schedulePoll);
});
</script>

<template>
  <el-drawer v-model="drawerVisible" :title="drawerTitle" size="52%" class="operation-log-drawer">
    <div class="operation-log-toolbar">
      <div class="operation-log-summary">
        <el-tag type="info" effect="light">
          {{ formatMessage(pageText.total, { total }) }}
        </el-tag>
        <el-tag v-if="foldedCount > 0" type="warning" effect="light">
          {{ formatMessage(pageText.folded, { count: foldedCount }) }}
        </el-tag>
      </div>
      <div class="operation-log-actions">
        <el-select v-model="levelFilter" size="small" class="operation-log-level-select">
          <el-option :label="pageText.allLevels" value="all" />
          <el-option :label="pageText.levels.info" value="info" />
          <el-option :label="pageText.levels.success" value="success" />
          <el-option :label="pageText.levels.warning" value="warning" />
          <el-option :label="pageText.levels.error" value="error" />
        </el-select>
        <el-switch v-model="autoRefresh" :active-text="pageText.autoRefresh" />
        <el-button :icon="Refresh" :loading="loading" @click="refreshLogs">{{ pageText.refresh }}</el-button>
        <el-button :icon="CopyDocument" @click="copyLogs">{{ pageText.copy }}</el-button>
        <el-button type="primary" :icon="Download" @click="downloadLogs">{{ pageText.exportTxt }}</el-button>
      </div>
    </div>

    <el-empty v-if="visibleLogs.length === 0" :description="emptyDescription" />
    <div v-else class="operation-log-list">
      <div v-for="item in visibleLogs" :key="item.id" class="operation-log-item">
        <span class="operation-log-time">{{ formatDateTime(item.created_at) }}</span>
        <el-tag :type="levelTagType(item.level)" effect="light" size="small">
          {{ levelLabel(item.level) }}
        </el-tag>
        <span class="operation-log-stage">{{ item.stage }}</span>
        <span v-if="item.progress_current !== null" class="operation-log-progress">
          {{ item.progress_current }}<template v-if="item.progress_total !== null">/{{ item.progress_total }}</template>
        </span>
        <span class="operation-log-message">{{ item.message }}</span>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.operation-log-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.operation-log-summary,
.operation-log-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.operation-log-level-select {
  width: 112px;
}

.operation-log-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: calc(100vh - 188px);
  overflow: auto;
  padding-right: 4px;
}

.operation-log-item {
  display: grid;
  grid-template-columns: 156px 72px minmax(92px, 140px) minmax(48px, auto) 1fr;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
  font-size: 13px;
}

.operation-log-time,
.operation-log-stage,
.operation-log-progress {
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.operation-log-message {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--el-text-color-primary);
}

@media (max-width: 900px) {
  .operation-log-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .operation-log-item {
    grid-template-columns: 1fr;
    align-items: flex-start;
  }
}
</style>

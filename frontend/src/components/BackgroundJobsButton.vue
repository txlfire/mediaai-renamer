<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Clock, Refresh, Document, CircleCheck } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { useBackgroundJobsStore } from "../stores/backgroundJobs";
import { useAuthStore } from "../stores/auth";
import { isSessionChangedError } from "../utils/sessionGeneration";
import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import OperationLogDrawer from "./OperationLogDrawer.vue";

const jobs = useBackgroundJobsStore();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const visible = ref(false);
const autoRefresh = ref(true);
const logVisible = ref(false);
const resultView = ref("summary");
const error = ref("");
const timer = ref<ReturnType<typeof setTimeout> | null>(null);
const text = messages.backgroundJobs;
const current = computed(() => jobs.activeJob);
const running = computed(() => jobs.recent.filter(job => ["queued", "running"].includes(job.status)).length);
const resultText = computed(() => jobs.resultRecord?.request_id === jobs.activeRequestId
  ? JSON.stringify(resultView.value === "items" ? jobs.resultRecord.items : jobs.resultRecord.result ?? jobs.resultRecord.items, null, 2) : "");
const kindLabel = (kind: string) => text.kinds[kind as keyof typeof text.kinds] || kind;
const statusLabel = (status: string) => text.statuses[status as keyof typeof text.statuses] || status;
const linkedResult = computed(() => jobs.resultRecord?.request_id === jobs.activeRequestId
  ? jobs.resultRecord.result as { operation_id?: number; plan_id?: number } | null : null);

async function openLinkedResult() {
  const value = linkedResult.value;
  if (!value || !current.value) return;
  const query = current.value.kind === "rename_dry_run" && Number.isSafeInteger(value.operation_id)
    ? { operation_id: String(value.operation_id) }
    : current.value.kind === "rollback_dry_run" && Number.isSafeInteger(value.plan_id) && Number.isSafeInteger(value.operation_id)
      ? { operation_id: String(value.operation_id), rollback_plan_id: String(value.plan_id) } : null;
  if (!query) return;
  visible.value = false;
  await router.push({ name: "rename-previews", query });
}

function clearTimer() {
  if (timer.value !== null) clearTimeout(timer.value);
  timer.value = null;
}

function schedule() {
  clearTimer();
  if (!visible.value || !autoRefresh.value || !jobs.activeRequestId || !current.value
    || !["queued", "running"].includes(current.value.status)) return;
  timer.value = setTimeout(async () => {
    if (!jobs.isObserved(jobs.activeRequestId)) await refresh(false);
    schedule();
  }, document.hidden ? 6000 : 1500);
}

async function refresh(loadRecent = true) {
  try {
    if (loadRecent) await jobs.loadRecent();
    if (jobs.activeRequestId) await jobs.refresh(jobs.activeRequestId);
    error.value = "";
  } catch (failure) {
    if (!isSessionChangedError(failure)) error.value = failure instanceof Error ? failure.message : messages.errors.unknown;
  }
}

async function showResult() {
  try {
    const result = await jobs.refresh(jobs.activeRequestId, true);
    resultView.value = (result.result as { result_truncated?: boolean } | null)?.result_truncated ? "items" : "summary";
    error.value = "";
  } catch (failure) {
    if (!isSessionChangedError(failure)) error.value = failure instanceof Error ? failure.message : text.resultCleared;
  }
}

async function acknowledge() {
  try {
    await ElMessageBox.confirm(text.acknowledgeWarning, text.acknowledge, { type: "warning" });
    jobs.acknowledge(jobs.activeRequestId);
  } catch (failure) {
    if (failure !== "cancel" && failure !== "close") error.value = failure instanceof Error ? failure.message : messages.errors.unknown;
    // 取消确认不改变任务及本浏览器的防重复记录。
  }
}

watch(() => [visible.value, autoRefresh.value, jobs.activeRequestId], async () => {
  clearTimer();
  if (!visible.value) logVisible.value = false;
  if (visible.value) {
    if (!jobs.isObserved(jobs.activeRequestId)) await refresh(false);
    schedule();
  }
});
watch(() => route.query.background_request_id, value => {
  if (typeof value === "string" && /^[\da-f-]{36}$/i.test(value)) {
    jobs.activeRequestId = value;
    visible.value = true;
  }
}, { immediate: true });
onMounted(() => { void refresh(); });
onBeforeUnmount(clearTimer);
</script>

<template>
  <el-button :icon="Clock" @click="visible = true">{{ text.title }}<span v-if="running"> ({{ running }})</span></el-button>
  <el-drawer v-model="visible" :title="text.title" size="min(880px, calc(100vw - 24px))" destroy-on-close>
    <div class="background-job-panel">
      <div class="background-job-toolbar">
        <el-select v-model="jobs.activeRequestId" :placeholder="text.recent">
          <el-option v-for="job in jobs.recent" :key="job.request_id" :value="job.request_id"
            :label="`#${job.id} ${kindLabel(job.kind)} · ${statusLabel(job.status)}`" />
        </el-select>
        <el-button :icon="Refresh" :aria-label="text.refresh" :title="text.refresh" @click="refresh()" />
        <el-checkbox v-model="autoRefresh">{{ messages.operationLogs.autoRefresh }}</el-checkbox>
      </div>
      <p v-if="error" class="background-job-error" role="alert">{{ error }}</p>
      <p v-if="!current && jobs.activeRequestId" class="background-job-request">{{ text.requestId }}：{{ jobs.activeRequestId }}</p>
      <template v-if="current">
        <el-descriptions :column="1" border>
          <el-descriptions-item :label="text.kind">{{ kindLabel(current.kind) }}</el-descriptions-item>
          <el-descriptions-item :label="messages.common.status">{{ statusLabel(current.status) }}</el-descriptions-item>
          <el-descriptions-item :label="text.requestId"><span class="background-job-request">{{ current.request_id }}</span></el-descriptions-item>
          <el-descriptions-item :label="text.progress">{{ current.completed_count }} / {{ current.total_count }}</el-descriptions-item>
        </el-descriptions>
        <el-progress :percentage="Math.min(100, Math.round(100 * current.completed_count / Math.max(1, current.total_count)))" />
        <p v-if="current.unknown_count" class="background-job-error">{{ formatMessage(text.unknown, { count: current.unknown_count }) }}</p>
        <p v-if="current.error_message" class="background-job-error">{{ current.error_message }}</p>
        <p v-if="current.result_cleared_at" class="background-job-error">{{ text.resultCleared }}</p>
        <div class="background-job-actions">
          <el-button :icon="Document" :disabled="!current.result_available" :title="current.result_cleared_at ? text.resultCleared : text.viewResult" @click="showResult">{{ text.viewResult }}</el-button>
          <el-button :icon="Document" :disabled="!auth.hasPermission('audit:read')" :title="auth.hasPermission('audit:read') ? text.viewLogs : messages.auth.permissionDenied" @click="logVisible = true">{{ text.viewLogs }}</el-button>
          <el-button v-if="jobs.hasPending(current.request_id) && !['queued', 'running'].includes(current.status)" :icon="CircleCheck" @click="acknowledge">{{ text.acknowledge }}</el-button>
        </div>
        <el-radio-group v-if="jobs.resultRecord?.request_id === jobs.activeRequestId" v-model="resultView" :aria-label="text.result">
          <el-radio-button value="summary">{{ text.summaryResult }}</el-radio-button>
          <el-radio-button value="items" :disabled="!jobs.resultRecord?.items?.length">{{ text.itemResults }}</el-radio-button>
        </el-radio-group>
        <pre v-if="resultText" class="background-job-result">{{ resultText }}</pre>
        <el-button v-if="linkedResult?.operation_id || linkedResult?.plan_id" :icon="Document" @click="openLinkedResult">{{ text.viewLinkedResult }}</el-button>
      </template>
      <el-empty v-else :description="text.empty" />
    </div>
  </el-drawer>
  <OperationLogDrawer v-model:visible="logVisible" task-type="background_job" :task-id="current?.id ?? null" :title="text.viewLogs" />
</template>

<style scoped>
.background-job-panel { display: grid; min-width: 0; gap: 16px; }
.background-job-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.background-job-toolbar .el-select { flex: 1 1 240px; min-width: 0; }
.background-job-request, .background-job-error { overflow-wrap: anywhere; }
.background-job-error { margin: 0; color: var(--semantic-warning); font-size: 13px; }
.background-job-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.background-job-result { margin: 0; padding: 12px; max-height: 50vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid var(--border-color); border-radius: 6px; background: var(--panel-soft-bg); }
</style>

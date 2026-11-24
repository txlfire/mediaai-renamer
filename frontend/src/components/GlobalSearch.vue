<script setup lang="ts">
import { Search, ArrowRight, Refresh } from "@element-plus/icons-vue";
import { computed, nextTick, onBeforeUnmount, ref, toRaw, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { searchKinds, searchLocal, readSearchRecord, searchDestination, type SearchGroup, type SearchItem, type SearchKind } from "../api/globalSearch";
import { useAuthStore } from "../stores/auth";
import { getSessionGeneration } from "../utils/sessionGeneration";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";

const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.globalSearch);
const emit = defineEmits<{ openBackground: [requestId: string] }>();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const visible = ref(false), query = ref(""), kind = ref<SearchKind | "all">("all");
const groups = ref<SearchGroup[]>([]), detail = ref<SearchItem | null>(null), loading = ref(false);
const errorKind = ref<"invalid" | "failed" | "unavailable" | null>(null);
const error = computed(() => errorKind.value ? text.value[errorKind.value] : "");
const composed = ref(false), searched = ref(false), beforeId = ref<number | null>(null);
const input = ref<{ focus: () => void }>();
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
let revision = 0;
let active = true;
const scope = computed(() => `${auth.currentUser?.id ?? ""}:${auth.currentUser?.permissions.join(",") ?? ""}`);
const empty = computed(() => searched.value && !loading.value && !error.value && !groups.value.some(group => group.items.length));
const label = (value: SearchKind) => text.value.kinds[value];
function title(value: SearchItem) {
  if (value.kind === "scan") return `${label(value.kind)} #${value.id}`;
  if (value.kind === "background") {
    return messages.value.backgroundJobs.kinds[value.title as keyof typeof messages.value.backgroundJobs.kinds] || label(value.kind);
  }
  return value.title;
}
const subtitle = (value: SearchItem) => value.kind === "background" ? `${label(value.kind)} #${value.id}` : value.subtitle;
const status = (value: string) => text.value.statuses[value as keyof typeof text.value.statuses] || value;

function cancel() {
  revision++;
  clearTimeout(timer);
  controller?.abort();
  controller = undefined;
  loading.value = false;
}
function clear() { cancel(); groups.value = []; detail.value = null; errorKind.value = null; searched.value = false; beforeId.value = null; }
function reset() { clear(); query.value = ""; kind.value = "all"; composed.value = false; }
function allowed() { return active && visible.value && Boolean(auth.currentUser) && !document.hidden; }
function current(token: number, session: number, owner: string) {
  return allowed() && token === revision && session === getSessionGeneration() && owner === scope.value;
}
function finish(token: number, session: number, owner: string) {
  if (token !== revision) return;
  if (session !== getSessionGeneration() || owner !== scope.value || !allowed()) { visible.value = false; reset(); }
  else loading.value = false;
}
async function open() { reset(); visible.value = true; await nextTick(); input.value?.focus(); }
function queue() {
  clear();
  if (!allowed() || composed.value) return;
  const length = [...query.value.trim().normalize("NFC")].length;
  if (length < 2 || length > 128) return;
  timer = setTimeout(() => { void run(); }, 300);
}
async function run(cursor: number | null = null) {
  cancel();
  if (!allowed() || composed.value) return;
  const term = query.value.trim().normalize("NFC");
  if ([...term].length < 2 || [...term].length > 128 || /\p{C}/u.test(term)) { errorKind.value = "invalid"; return; }
  const token = revision, session = getSessionGeneration(), owner = scope.value;
  controller = new AbortController(); loading.value = true; errorKind.value = null; detail.value = null; groups.value = []; searched.value = true;
  try {
    const result = await searchLocal(term, kind.value, cursor, controller.signal);
    if (!current(token, session, owner)) return;
    groups.value = result; beforeId.value = cursor;
  } catch {
    if (current(token, session, owner)) errorKind.value = "failed";
  } finally { finish(token, session, owner); }
}
async function choose(value: SearchItem) {
  cancel();
  if (!allowed()) return;
  const token = revision, session = getSessionGeneration(), owner = scope.value;
  controller = new AbortController(); loading.value = true; errorKind.value = null;
  try {
    const result = await readSearchRecord(value.kind, value.id, controller.signal);
    if (current(token, session, owner)) { detail.value = result; return result; }
  } catch { if (current(token, session, owner)) { groups.value = []; detail.value = null; errorKind.value = "unavailable"; } }
  finally { finish(token, session, owner); }
}
async function navigate() {
  if (!allowed() || !detail.value) return;
  // 跳转前重读稳定 ID；不使用服务端 URL，也不把搜索词带到地址栏。
  const previous = detail.value;
  const session = getSessionGeneration(), owner = scope.value;
  const record = await choose(previous);
  if (!record || toRaw(detail.value) !== record || error.value || !allowed()
    || session !== getSessionGeneration() || owner !== scope.value) return;
  if (record.kind === "background" && record.requestId) {
    visible.value = false;
    emit("openBackground", record.requestId);
    return;
  }
  const destination = searchDestination(record);
  if (!destination) { errorKind.value = "unavailable"; return; }
  visible.value = false;
  await router.push(destination);
}
function more(group: SearchGroup) {
  if (kind.value === "all") kind.value = group.kind;
  else void run(group.nextBeforeId);
}
function hidden() { if (document.hidden) { visible.value = false; reset(); } }
watch(query, queue, { flush: "sync" });
watch(kind, () => { clear(); if (query.value.trim().length >= 2) void run(); });
watch(visible, value => { if (!value) reset(); });
watch(scope, () => { visible.value = false; reset(); }, { flush: "sync" });
watch(() => route.fullPath, () => { visible.value = false; reset(); });
document.addEventListener("visibilitychange", hidden);
onBeforeUnmount(() => { active = false; reset(); document.removeEventListener("visibilitychange", hidden); });
</script>

<template>
  <el-button class="global-search-trigger" :icon="Search" :aria-label="text.open" @click="open">{{ text.open }}</el-button>
  <el-dialog v-model="visible" :title="text.open" width="min(820px, calc(100vw - 24px))" class="global-search-dialog" destroy-on-close>
    <div class="global-search-form">
      <el-input ref="input" v-model="query" :prefix-icon="Search" clearable maxlength="128" :aria-label="text.input" :placeholder="text.placeholder"
        @compositionstart="composed = true; cancel()" @compositionend="composed = false; queue()" @keydown.enter.prevent="!$event.isComposing && run()" />
      <el-button :icon="Search" :loading="loading" @click="run()">{{ text.submit }}</el-button>
    </div>
    <el-radio-group v-model="kind" class="global-search-kinds" :aria-label="text.category">
      <el-radio-button value="all">{{ text.all }}</el-radio-button>
      <el-radio-button v-for="value in searchKinds" :key="value" :value="value">{{ label(value) }}</el-radio-button>
    </el-radio-group>
    <p class="global-search-scope">{{ text.scope }}</p>
    <p v-if="error" role="alert" class="global-search-error">{{ error }}</p>
    <div class="global-search-results" :aria-busy="loading">
      <p v-if="loading" role="status">{{ text.loading }}</p>
      <template v-else-if="detail">
        <div class="global-search-detail">
          <span class="global-search-eyebrow">{{ label(detail.kind) }} · #{{ detail.id }}</span>
          <h3>{{ title(detail) }}</h3>
          <p>{{ subtitle(detail) }}</p>
          <dl><dt>{{ text.state }}</dt><dd>{{ status(detail.status) }}</dd>
            <template v-if="detail.sourceId"><dt>{{ text.sourceId }}</dt><dd>{{ detail.sourceId }}</dd></template>
            <template v-if="detail.scanJobId"><dt>{{ text.scanJobId }}</dt><dd>{{ detail.scanJobId }}</dd></template>
            <template v-if="detail.requestId"><dt>{{ text.requestId }}</dt><dd>{{ detail.requestId }}</dd></template>
          </dl>
          <el-button @click="detail = null">{{ text.back }}</el-button>
          <el-button type="primary" :icon="ArrowRight" @click="navigate">{{ detail.kind === 'background' ? text.openTask : text.openList }}</el-button>
        </div>
      </template>
      <template v-else>
        <section v-for="group in groups.filter(value => value.items.length)" :key="group.kind" class="global-search-group">
          <h3>{{ label(group.kind) }}</h3>
          <button v-for="row in group.items" :key="row.id" type="button" class="global-search-row" @click="choose(row)">
            <span class="global-search-row-copy"><strong>{{ title(row) }}</strong><span>{{ subtitle(row) }}</span></span>
            <span class="global-search-row-meta">#{{ row.id }} · {{ status(row.status) }}</span><el-icon><ArrowRight /></el-icon>
          </button>
          <el-button v-if="group.hasMore" link :icon="ArrowRight" @click="more(group)">{{ kind === 'all' ? text.moreCategory : text.next }}</el-button>
        </section>
        <el-empty v-if="empty" :description="text.empty" :image-size="64" />
        <p v-if="!searched" class="global-search-idle">{{ text.idle }}</p>
      </template>
    </div>
    <template #footer>
      <el-button v-if="beforeId" :icon="Refresh" @click="run()">{{ text.firstPage }}</el-button>
      <el-button @click="visible = false">{{ messages.common.close }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.global-search-form { display: flex; gap: 8px; }
.global-search-form .el-input { min-width: 0; flex: 1; }
.global-search-kinds { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 12px; }
.global-search-scope, .global-search-idle, .global-search-row-copy > span, .global-search-eyebrow, .global-search-row-meta { color: var(--text-muted); font-size: 12px; }
.global-search-scope { margin: 10px 0; line-height: 1.5; }
.global-search-error { color: var(--semantic-danger); font-size: 13px; }
.global-search-results { min-height: 120px; }
.global-search-group + .global-search-group { margin-top: 16px; }
.global-search-group h3 { margin: 0 0 6px; font-size: 13px; color: var(--text-muted); }
.global-search-row { display: flex; align-items: center; gap: 12px; padding: 10px 8px; width: 100%; min-width: 0; border: 0; border-bottom: 1px solid var(--border-color); background: transparent; color: var(--text-main); cursor: pointer; text-align: left; font: inherit; }
.global-search-row:hover { background: var(--panel-soft-bg); }
.global-search-row:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: -2px; }
.global-search-row-copy { flex: 1; min-width: 0; display: grid; gap: 3px; }
.global-search-row-copy > * { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.global-search-row-copy strong { font-size: 14px; font-weight: 500; }
.global-search-row-meta { flex: 0 1 140px; overflow-wrap: anywhere; }
.global-search-detail { overflow-wrap: anywhere; }
.global-search-detail h3 { margin: 8px 0; font-size: 18px; }
.global-search-detail dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px 20px; font-size: 13px; }
.global-search-detail dt { color: var(--text-muted); }
.global-search-detail dd { margin: 0; }
</style>

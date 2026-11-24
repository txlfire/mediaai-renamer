<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { apiClient } from "../api/client";
import { useAuthStore } from "../stores/auth";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { getSessionGeneration } from "../utils/sessionGeneration";

type PortPair = { frontendPort: number; backendPort: number };
type Snapshot = { mode: "deployment" } | {
  mode: "local-dev"; active: PortPair; desired: PortPair; revision: number; restartRequired: boolean;
};
type ErrorKind = "unavailable" | "invalid" | "frontendUnavailable" | "backendUnavailable" | "changed" | "unconfirmed" | "exhausted";
const { messages } = useInterfaceLanguage();
const text = computed(() => messages.value.settings.servicePorts);
const auth = useAuthStore();
const scope = computed(() => auth.hasPermission("settings:write") ? auth.currentUser?.id : null);
const model = ref<Snapshot>();
const frontendPort = ref<number>();
const backendPort = ref<number>();
const busy = ref(false);
const saved = ref(false);
const error = ref<ErrorKind>();
const origin = window.location.origin;
const fromDevFrontend = import.meta.env.DEV;
let request: AbortController | undefined;

const isPort = (value?: number): value is number => value !== undefined && Number.isInteger(value) && value >= 1024 && value <= 65535;
const editable = computed(() => Boolean(scope.value && model.value?.mode === "local-dev" && !busy.value));
const dirty = computed(() => model.value?.mode === "local-dev"
  && (model.value.revision === 0 || frontendPort.value !== model.value.desired.frontendPort
    || backendPort.value !== model.value.desired.backendPort));

function cancel() {
  request?.abort();
  request = undefined;
  busy.value = false;
}

async function exchange(update?: PortPair & { expectedRevision: number }) {
  if (!scope.value || busy.value) return;
  const controller = new AbortController();
  const generation = getSessionGeneration();
  request = controller;
  busy.value = true;
  error.value = undefined;
  saved.value = false;
  const active = () => request === controller && generation === getSessionGeneration();
  try {
    const config = { signal: controller.signal };
    const response = update
      ? await apiClient.put<Snapshot>("/settings/service-ports", update, config)
      : await apiClient.get<Snapshot>("/settings/service-ports", config);
    if (!active()) return;
    model.value = response.data;
    if (response.data.mode === "local-dev") {
      frontendPort.value = response.data.desired.frontendPort;
      backendPort.value = response.data.desired.backendPort;
    }
    saved.value = Boolean(update);
  } catch (failure) {
    if (!active()) return;
    const code = (failure as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (code === "frontend_port_unavailable" || code === "backend_port_unavailable" || code === "invalid_service_ports") {
      error.value = code === "frontend_port_unavailable" ? "frontendUnavailable"
        : code === "backend_port_unavailable" ? "backendUnavailable" : "invalid";
    } else {
      model.value = undefined;
      error.value = code === "service_ports_changed" ? "changed"
        : code === "service_ports_revision_exhausted" ? "exhausted" : update ? "unconfirmed" : "unavailable";
    }
  } finally {
    if (request === controller) cancel();
  }
}

function save() {
  const state = model.value;
  const frontend = frontendPort.value, backend = backendPort.value;
  if (!editable.value || state?.mode !== "local-dev") return;
  if (!isPort(frontend) || !isPort(backend) || frontend === backend) {
    error.value = "invalid";
    return;
  }
  void exchange({ frontendPort: frontend, backendPort: backend, expectedRevision: state.revision });
}

onMounted(() => { void exchange(); });
watch(scope, () => {
  cancel();
  model.value = undefined;
  frontendPort.value = undefined;
  backendPort.value = undefined;
  error.value = undefined;
  saved.value = false;
  void exchange();
}, { flush: "sync" });
onBeforeUnmount(cancel);
</script>

<template>
  <section class="port-settings" :aria-label="text.title" :aria-busy="busy">
    <header class="port-settings-header">
      <h3>{{ text.title }}</h3>
      <el-tag v-if="model" type="info" effect="plain">{{ model.mode === 'local-dev' ? text.localMode : text.deploymentMode }}</el-tag>
      <el-tag v-if="model?.mode === 'local-dev' && model.restartRequired" type="warning">{{ text.pending }}</el-tag>
    </header>
    <p class="port-settings-origin">{{ text.access }} <strong>{{ origin }}</strong></p>
    <p v-if="model?.mode === 'deployment'" class="port-settings-description">{{ fromDevFrontend ? text.unmanagedLocal : text.deployment }}</p>
    <div v-else-if="model?.mode === 'local-dev'" class="port-settings-fields">
      <el-form-item :label="text.frontend">
        <el-input-number v-model="frontendPort" :min="1024" :max="65535" :controls="false" :disabled="!editable" />
        <small>{{ text.active }} {{ model.active.frontendPort }}</small>
      </el-form-item>
      <el-form-item :label="text.backend">
        <el-input-number v-model="backendPort" :min="1024" :max="65535" :controls="false" :disabled="!editable" />
        <small>{{ text.active }} {{ model.active.backendPort }}</small>
      </el-form-item>
    </div>
    <p v-if="error" role="alert" class="port-settings-error">{{ text[error] }}</p>
    <p v-else-if="saved" role="status" class="port-settings-description">{{ text.saved }}</p>
    <p v-else-if="model?.mode === 'local-dev'" class="port-settings-description">{{ text.nextStart }}</p>
    <div v-if="model?.mode !== 'deployment'" class="port-settings-actions">
      <el-button :icon="Refresh" :loading="busy" @click="exchange()">{{ messages.common.refresh }}</el-button>
      <el-button v-if="model?.mode === 'local-dev'" type="primary" :disabled="!editable || !dirty" @click="save">{{ text.save }}</el-button>
    </div>
  </section>
</template>

<style scoped>
.port-settings { min-width: 0; border-bottom: 1px solid var(--border-color); padding-bottom: 16px; }
.port-settings-header, .port-settings-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.port-settings-header h3 { margin: 0; font-size: 15px; }
.port-settings-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 12px; }
.port-settings-fields small, .port-settings-description { color: var(--text-muted); }
.port-settings-fields small { display: block; flex-basis: 100%; }
.port-settings-origin { overflow-wrap: anywhere; }
.port-settings-error { color: var(--semantic-danger); }
.port-settings-description, .port-settings-error, .port-settings-origin { font-size: 13px; }
</style>

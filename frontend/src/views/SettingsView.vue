<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

import ListPageLayout from "../components/ListPageLayout.vue";
import { zhCnMessages as messages } from "../locales/zh-CN";
import { useSettingsStore } from "../stores/settings";

const settingsStore = useSettingsStore();
const activeCategory = ref("tmdb");
const pageText = messages.settings;

const form = reactive({
  apiKey: "",
  language: "zh-CN",
  region: "CN",
  timeoutSeconds: "10",
  enabled: false,
  priority: 1,
  minimumFileSizeMb: "0",
});

const categories = computed(() => [
  { key: "tmdb", label: pageText.categories.tmdb },
  { key: "naming", label: pageText.categories.naming },
  { key: "scan", label: pageText.categories.scan },
  { key: "operations", label: pageText.categories.operations },
]);

function settingValue<T>(key: string, fallback: T): T {
  return (settingsStore.settingMap[key]?.value ?? fallback) as T;
}

function syncForm() {
  form.v4Token = settingValue("tmdb.v4_token", "");
  form.apiKey = "";
  form.language = settingValue("tmdb.language", "zh-CN");
  form.region = settingValue("tmdb.region", "CN");
  form.timeoutSeconds = String(Math.max(1, Math.round(Number(settingValue("tmdb.timeout_ms", 10000)) / 1000)));
  form.enabled = Boolean(settingValue("tmdb.enabled", false));
  form.priority = Number(settingValue("tmdb.priority", 1));
  form.minimumFileSizeMb = String(Math.round(Number(settingValue("scan.minimum_file_size", 0)) / 1024 / 1024));
}

function onlyDigits(key: "timeoutSeconds" | "minimumFileSizeMb") {
  form[key] = form[key].replace(/\D/g, "");
}

async function saveTmdbSettings() {
  const timeoutSeconds = Math.min(30, Math.max(1, Number(form.timeoutSeconds || 10)));
  const priority = Math.min(100, Math.max(0, Number(form.priority || 0)));
  const minimumFileSize = Math.max(0, Number(form.minimumFileSizeMb || 0)) * 1024 * 1024;
  form.timeoutSeconds = String(timeoutSeconds);
  form.priority = priority;
  form.minimumFileSizeMb = String(Math.round(minimumFileSize / 1024 / 1024));
  const values: Record<string, string | number | boolean> = {
    "tmdb.v4_token": form.v4Token.trim(),
    "tmdb.language": form.language,
    "tmdb.region": form.region,
    "tmdb.timeout_ms": timeoutSeconds * 1000,
    "tmdb.enabled": form.enabled,
    "tmdb.priority": priority,
    "scan.minimum_file_size": minimumFileSize,
  };
  if (form.apiKey.trim()) {
    values["tmdb.api_key"] = form.apiKey.trim();
  }

  await settingsStore.saveSettings(values);
  syncForm();
  ElMessage.success(pageText.saved);
}

const testResult = ref<import("../api/client").TmdbConnectionTestResult | null>(null);

function channelStatusText(status: string): string {
  if (status === "success") return "成功";
  if (status === "failed") return "失败";
  return "未配置";
}

function channelStatusType(status: string): "success" | "danger" | "info" {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "info";
}

function effectiveChannelLabel(effective: string): string {
  if (effective === "v4") return "V4";
  if (effective === "v3") return "V3";
  return "无可用通道";
}

async function testTmdbConnection() {
  try {
    const result = await settingsStore.testTmdbSettings();
    testResult.value = result;
  } catch (error) {
    testResult.value = {
      v4: { status: "failed", message: String(error) },
      v3: { status: "failed", message: String(error) },
      effective: "none",
    };
  }
}

onMounted(async () => {
  await settingsStore.loadSettings();
  syncForm();
});
</script>

<template>
  <ListPageLayout :title="pageText.title" :description="pageText.description">
    <el-alert v-if="settingsStore.errorMessage" type="error" :title="settingsStore.errorMessage" show-icon />

    <div class="settings-shell">
      <aside class="settings-categories">
        <button
          v-for="category in categories"
          :key="category.key"
          type="button"
          class="settings-category-button"
          :class="{ 'is-active': activeCategory === category.key }"
          @click="activeCategory = category.key"
        >
          {{ category.label }}
        </button>
      </aside>

      <section class="settings-panel">
        <el-form v-if="activeCategory === 'tmdb'" label-position="top" class="settings-form">
          <el-alert
            :title="pageText.tmdb.priorityHint"
            type="info"
            :closable="false"
            show-icon
          />

          <el-form-item :label="pageText.tmdb.v4Token">
            <el-input
              v-model="form.v4Token"
              class="settings-secret-control"
              :placeholder="pageText.tmdb.v4TokenPlaceholder"
              show-password
              clearable
            />
            <span class="setting-source">{{ pageText.tmdb.v4TokenHint }}</span>
          </el-form-item>

          <el-form-item :label="pageText.tmdb.apiKey">
            <el-input
              v-model="form.apiKey"
              class="settings-secret-control"
              :placeholder="(settingsStore.settingMap['tmdb.api_key']?.value as string) || pageText.tmdb.apiKeyPlaceholder"
              show-password
              clearable
            />
          </el-form-item>

          <div class="settings-grid">
            <el-form-item :label="pageText.tmdb.language">
              <el-select v-model="form.language" class="settings-code-control">
                <el-option label="zh-CN" value="zh-CN" />
                <el-option label="en-US" value="en-US" />
              </el-select>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.region">
              <el-select v-model="form.region" class="settings-code-control">
                <el-option label="CN" value="CN" />
                <el-option label="US" value="US" />
                <el-option label="HK" value="HK" />
                <el-option label="TW" value="TW" />
                <el-option label="JP" value="JP" />
                <el-option label="KR" value="KR" />
              </el-select>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.timeout">
              <el-input v-model="form.timeoutSeconds" class="settings-number-control" maxlength="6" @input="onlyDigits('timeoutSeconds')">
                <template #append>{{ pageText.tmdb.seconds }}</template>
              </el-input>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.priority">
              <el-input-number
                v-model="form.priority"
                class="settings-short-control"
                :min="0"
                :max="100"
                :step="1"
                :precision="0"
                controls-position="right"
              />
            </el-form-item>

            <el-form-item :label="pageText.tmdb.minimumFileSize">
              <el-input v-model="form.minimumFileSizeMb" class="settings-threshold-control" maxlength="4" @input="onlyDigits('minimumFileSizeMb')">
                <template #append>{{ pageText.tmdb.megabytes }}</template>
              </el-input>
            </el-form-item>

            <el-form-item :label="pageText.tmdb.enabled">
              <el-switch v-model="form.enabled" />
            </el-form-item>
          </div>

          <div v-if="testResult" class="settings-test-results">
            <el-alert
              :title="'V4 ' + pageText.tmdb.testConnection + ' ' + channelStatusText(testResult.v4.status)"
              :description="testResult.v4.message || channelStatusText(testResult.v4.status)"
              :type="channelStatusType(testResult.v4.status)"
              show-icon
              :closable="false"
            />
            <el-alert
              :title="'V3 ' + pageText.tmdb.testConnection + ' ' + channelStatusText(testResult.v3.status)"
              :description="testResult.v3.message || channelStatusText(testResult.v3.status)"
              :type="channelStatusType(testResult.v3.status)"
              show-icon
              :closable="false"
            />
            <el-alert
              v-if="testResult.effective !== 'none'"
              :title="pageText.tmdb.testConnection + ' ' + effectiveChannelLabel(testResult.effective)"
              type="success"
              show-icon
              :closable="false"
            />
          </div>

          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button :loading="settingsStore.loading" @click="testTmdbConnection">
              {{ pageText.tmdb.testConnection }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveTmdbSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>

        <el-empty v-else :description="pageText.reserved" />
      </section>
    </div>
  </ListPageLayout>
</template>



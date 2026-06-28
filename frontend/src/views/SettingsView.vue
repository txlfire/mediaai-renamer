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
  v4Token: "",
  apiKey: "",
  language: "zh-CN",
  region: "CN",
  timeoutSeconds: "10",
  enabled: false,
  priority: 1,
  minimumFileSizeMb: "0",
  scanBatchSize: "100",
  scanBatchIntervalSeconds: "1",
  scanSkipHiddenFiles: true,
  scanRecursive: true,
  scanValidatePathBeforeScan: true,
  movieTemplate: "{title}.{year}",
  episodeTemplate: "{title}.{year}.S{season:02d}E{episode:02d}",
  namingSeparator: ".",
  keepYear: true,
  cleanIllegalChars: true,
  textTruncateBytes: "50",
  pathTruncateBytes: "80",
  logRetentionDays: "30",
  logDefaultLimit: "200",
  forceDryRun: true,
  requireSecondConfirmation: true,
  persistFailureDetail: true,
  batchLimit: "200",
  sharedDefaultPathType: "local",
  sharedConnectionTimeoutSeconds: "5",
  sharedDirectoryBrowseLimit: "500",
  forceScanConnectionTest: true,
  forceRenameWriteTest: true,
  nfsOperationTimeoutSeconds: "30",
  nfsRetryCount: "3",
  preferNfsv4: true,
  mountCheckIntervalSeconds: "60",
});

const categories = computed(() => [
  { key: "tmdb", label: pageText.categories.tmdb },
  { key: "naming", label: pageText.categories.naming },
  { key: "scan", label: pageText.categories.scan },
  { key: "operations", label: pageText.categories.operations },
  { key: "shared", label: pageText.categories.shared },
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
  form.scanBatchSize = String(settingValue("scan.batch_size", 100));
  form.scanBatchIntervalSeconds = String(settingValue("scan.batch_interval_seconds", 1));
  form.scanSkipHiddenFiles = Boolean(settingValue("scan.skip_hidden_files", true));
  form.scanRecursive = Boolean(settingValue("scan.recursive", true));
  form.scanValidatePathBeforeScan = Boolean(settingValue("scan.validate_path_before_scan", true));
  form.movieTemplate = String(settingValue("naming.movie_template", "{title}.{year}"));
  form.episodeTemplate = String(settingValue("naming.episode_template", "{title}.{year}.S{season:02d}E{episode:02d}"));
  form.namingSeparator = String(settingValue("naming.separator", "."));
  form.keepYear = Boolean(settingValue("naming.keep_year", true));
  form.cleanIllegalChars = Boolean(settingValue("naming.clean_illegal_chars", true));
  form.textTruncateBytes = String(settingValue("naming.text_truncate_bytes", 50));
  form.pathTruncateBytes = String(settingValue("naming.path_truncate_bytes", 80));
  form.logRetentionDays = String(settingValue("operations.log_retention_days", 30));
  form.logDefaultLimit = String(settingValue("operations.log_default_limit", 200));
  form.forceDryRun = Boolean(settingValue("operations.force_dry_run", true));
  form.requireSecondConfirmation = Boolean(settingValue("operations.require_second_confirmation", true));
  form.persistFailureDetail = Boolean(settingValue("operations.persist_failure_detail", true));
  form.batchLimit = String(settingValue("operations.batch_limit", 200));
  form.sharedDefaultPathType = String(settingValue("shared.default_path_type", "local"));
  form.sharedConnectionTimeoutSeconds = String(settingValue("shared.connection_timeout_seconds", 5));
  form.sharedDirectoryBrowseLimit = String(settingValue("shared.directory_browse_limit", 500));
  form.forceScanConnectionTest = Boolean(settingValue("shared.force_scan_connection_test", true));
  form.forceRenameWriteTest = Boolean(settingValue("shared.force_rename_write_test", true));
  form.nfsOperationTimeoutSeconds = String(settingValue("shared.nfs_operation_timeout_seconds", 30));
  form.nfsRetryCount = String(settingValue("shared.nfs_retry_count", 3));
  form.preferNfsv4 = Boolean(settingValue("shared.prefer_nfsv4", true));
  form.mountCheckIntervalSeconds = String(settingValue("shared.mount_check_interval_seconds", 60));
}

function onlyDigits(
  key:
    | "timeoutSeconds"
    | "minimumFileSizeMb"
    | "scanBatchSize"
    | "scanBatchIntervalSeconds"
    | "textTruncateBytes"
    | "pathTruncateBytes"
    | "logRetentionDays"
    | "logDefaultLimit"
    | "batchLimit"
    | "sharedConnectionTimeoutSeconds"
    | "sharedDirectoryBrowseLimit"
    | "nfsOperationTimeoutSeconds"
    | "nfsRetryCount"
    | "mountCheckIntervalSeconds",
) {
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

async function saveScanSettings() {
  const minimumFileSize = Math.max(0, Number(form.minimumFileSizeMb || 0)) * 1024 * 1024;
  await settingsStore.saveSettings({
    "scan.batch_size": Number(form.scanBatchSize || 100),
    "scan.batch_interval_seconds": Number(form.scanBatchIntervalSeconds || 0),
    "scan.minimum_file_size": minimumFileSize,
    "scan.skip_hidden_files": form.scanSkipHiddenFiles,
    "scan.recursive": form.scanRecursive,
    "scan.validate_path_before_scan": form.scanValidatePathBeforeScan,
  });
  syncForm();
  ElMessage.success(pageText.saved);
}

async function saveNamingSettings() {
  await settingsStore.saveSettings({
    "naming.movie_template": form.movieTemplate.trim(),
    "naming.episode_template": form.episodeTemplate.trim(),
    "naming.separator": form.namingSeparator.trim(),
    "naming.keep_year": form.keepYear,
    "naming.clean_illegal_chars": form.cleanIllegalChars,
    "naming.text_truncate_bytes": Number(form.textTruncateBytes || 50),
    "naming.path_truncate_bytes": Number(form.pathTruncateBytes || 80),
  });
  syncForm();
  ElMessage.success(pageText.saved);
}

async function saveOperationSettings() {
  await settingsStore.saveSettings({
    "operations.log_retention_days": Number(form.logRetentionDays || 30),
    "operations.log_default_limit": Number(form.logDefaultLimit || 200),
    "operations.force_dry_run": true,
    "operations.require_second_confirmation": true,
    "operations.persist_failure_detail": form.persistFailureDetail,
    "operations.batch_limit": Number(form.batchLimit || 200),
  });
  syncForm();
  ElMessage.success(pageText.saved);
}

async function saveSharedSettings() {
  await settingsStore.saveSettings({
    "shared.default_path_type": form.sharedDefaultPathType,
    "shared.connection_timeout_seconds": Number(form.sharedConnectionTimeoutSeconds || 5),
    "shared.directory_browse_limit": Number(form.sharedDirectoryBrowseLimit || 500),
    "shared.force_scan_connection_test": form.forceScanConnectionTest,
    "shared.force_rename_write_test": form.forceRenameWriteTest,
    "shared.nfs_operation_timeout_seconds": Number(form.nfsOperationTimeoutSeconds || 30),
    "shared.nfs_retry_count": Number(form.nfsRetryCount || 3),
    "shared.prefer_nfsv4": form.preferNfsv4,
    "shared.mount_check_interval_seconds": Number(form.mountCheckIntervalSeconds || 60),
  });
  syncForm();
  ElMessage.success(pageText.saved);
}

const testResult = ref<import("../api/client").TmdbConnectionTestResult | null>(null);

function channelStatusText(status: string): string {
  if (status === "success") return pageText.tmdb.statusSuccess;
  if (status === "failed") return pageText.tmdb.statusFailed;
  return pageText.tmdb.statusNotConfigured;
}

function channelStatusType(status: string): "success" | "danger" | "info" {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "info";
}

function effectiveChannelLabel(effective: string): string {
  if (effective === "v4") return "V4";
  if (effective === "v3") return "V3";
  return pageText.tmdb.noAvailableChannel;
}

const testChannels = computed(() => {
  if (!testResult.value) {
    return [];
  }
  return [
    { key: "v4", label: "V4", result: testResult.value.v4 },
    { key: "v3", label: "V3", result: testResult.value.v3 },
  ];
});

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

          <div v-if="testResult" class="settings-test-results">
            <div class="settings-test-results-title">{{ pageText.tmdb.testResultTitle }}</div>
            <div
              v-for="channel in testChannels"
              :key="channel.key"
              class="settings-test-result-row"
            >
              <span class="settings-test-channel">{{ channel.label }}</span>
              <el-tag :type="channelStatusType(channel.result.status)" effect="light">
                {{ channelStatusText(channel.result.status) }}
              </el-tag>
              <span class="settings-test-message">
                {{ channel.result.message || channelStatusText(channel.result.status) }}
              </span>
            </div>
            <div v-if="testResult.effective !== 'none'" class="settings-test-effective">
              <span>{{ pageText.tmdb.effectiveChannel }}</span>
              <strong>{{ effectiveChannelLabel(testResult.effective) }}</strong>
            </div>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'scan'" label-position="top" class="settings-form">
          <div class="settings-grid">
            <el-form-item :label="pageText.scan.batchSize">
              <el-input v-model="form.scanBatchSize" maxlength="5" @input="onlyDigits('scanBatchSize')" />
            </el-form-item>
            <el-form-item :label="pageText.scan.batchInterval">
              <el-input v-model="form.scanBatchIntervalSeconds" maxlength="4" @input="onlyDigits('scanBatchIntervalSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.scan.minimumFileSize">
              <el-input v-model="form.minimumFileSizeMb" maxlength="4" @input="onlyDigits('minimumFileSizeMb')">
                <template #append>{{ pageText.units.megabytes }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.scan.skipHiddenFiles">
              <el-switch v-model="form.scanSkipHiddenFiles" />
            </el-form-item>
            <el-form-item :label="pageText.scan.recursive">
              <el-switch v-model="form.scanRecursive" />
            </el-form-item>
            <el-form-item :label="pageText.scan.validatePathBeforeScan">
              <el-switch v-model="form.scanValidatePathBeforeScan" />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveScanSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'naming'" label-position="top" class="settings-form">
          <el-form-item :label="pageText.naming.movieTemplate">
            <el-input v-model="form.movieTemplate" />
          </el-form-item>
          <el-form-item :label="pageText.naming.episodeTemplate">
            <el-input v-model="form.episodeTemplate" />
          </el-form-item>
          <div class="settings-grid">
            <el-form-item :label="pageText.naming.separator">
              <el-input v-model="form.namingSeparator" maxlength="4" />
            </el-form-item>
            <el-form-item :label="pageText.naming.textTruncateBytes">
              <el-input v-model="form.textTruncateBytes" maxlength="4" @input="onlyDigits('textTruncateBytes')" />
            </el-form-item>
            <el-form-item :label="pageText.naming.pathTruncateBytes">
              <el-input v-model="form.pathTruncateBytes" maxlength="4" @input="onlyDigits('pathTruncateBytes')" />
            </el-form-item>
            <el-form-item :label="pageText.naming.keepYear">
              <el-switch v-model="form.keepYear" />
            </el-form-item>
            <el-form-item :label="pageText.naming.cleanIllegalChars">
              <el-switch v-model="form.cleanIllegalChars" disabled />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveNamingSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'operations'" label-position="top" class="settings-form">
          <div class="settings-grid">
            <el-form-item :label="pageText.operations.logRetentionDays">
              <el-input v-model="form.logRetentionDays" maxlength="4" @input="onlyDigits('logRetentionDays')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.logDefaultLimit">
              <el-input v-model="form.logDefaultLimit" maxlength="5" @input="onlyDigits('logDefaultLimit')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.batchLimit">
              <el-input v-model="form.batchLimit" maxlength="5" @input="onlyDigits('batchLimit')" />
            </el-form-item>
            <el-form-item :label="pageText.operations.forceDryRun">
              <el-switch v-model="form.forceDryRun" disabled />
            </el-form-item>
            <el-form-item :label="pageText.operations.requireSecondConfirmation">
              <el-switch v-model="form.requireSecondConfirmation" disabled />
            </el-form-item>
            <el-form-item :label="pageText.operations.persistFailureDetail">
              <el-switch v-model="form.persistFailureDetail" />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveOperationSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>

        <el-form v-else-if="activeCategory === 'shared'" label-position="top" class="settings-form">
          <div class="settings-grid">
            <el-form-item :label="pageText.shared.defaultPathType">
              <el-select v-model="form.sharedDefaultPathType">
                <el-option :label="messages.mediaSources.pathTypes.local" value="local" />
                <el-option :label="messages.mediaSources.pathTypes.unc" value="unc" />
                <el-option :label="messages.mediaSources.pathTypes.mountedNfs" value="mounted_nfs" />
              </el-select>
            </el-form-item>
            <el-form-item :label="pageText.shared.connectionTimeout">
              <el-input v-model="form.sharedConnectionTimeoutSeconds" maxlength="4" @input="onlyDigits('sharedConnectionTimeoutSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.shared.directoryBrowseLimit">
              <el-input v-model="form.sharedDirectoryBrowseLimit" maxlength="5" @input="onlyDigits('sharedDirectoryBrowseLimit')" />
            </el-form-item>
            <el-form-item :label="pageText.shared.nfsOperationTimeout">
              <el-input v-model="form.nfsOperationTimeoutSeconds" maxlength="4" @input="onlyDigits('nfsOperationTimeoutSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.shared.nfsRetryCount">
              <el-input v-model="form.nfsRetryCount" maxlength="2" @input="onlyDigits('nfsRetryCount')" />
            </el-form-item>
            <el-form-item :label="pageText.shared.mountCheckInterval">
              <el-input v-model="form.mountCheckIntervalSeconds" maxlength="5" @input="onlyDigits('mountCheckIntervalSeconds')">
                <template #append>{{ pageText.units.seconds }}</template>
              </el-input>
            </el-form-item>
            <el-form-item :label="pageText.shared.forceScanConnectionTest">
              <el-switch v-model="form.forceScanConnectionTest" />
            </el-form-item>
            <el-form-item :label="pageText.shared.forceRenameWriteTest">
              <el-switch v-model="form.forceRenameWriteTest" />
            </el-form-item>
            <el-form-item :label="pageText.shared.preferNfsv4">
              <el-switch v-model="form.preferNfsv4" />
            </el-form-item>
          </div>
          <div class="settings-actions">
            <el-button :loading="settingsStore.loading" @click="settingsStore.loadSettings().then(syncForm)">
              {{ messages.common.refresh }}
            </el-button>
            <el-button type="primary" :loading="settingsStore.loading" @click="saveSharedSettings">
              {{ messages.common.save }}
            </el-button>
          </div>
        </el-form>
      </section>
    </div>
  </ListPageLayout>
</template>


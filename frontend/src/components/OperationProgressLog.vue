<script setup lang="ts">
import { zhCnMessages as messages } from "../locales/zh-CN";

defineProps<{
  visible: boolean;
  percentage: number;
  text: string;
  logs: string[];
  summary?: string;
}>();
</script>

<template>
  <div v-if="visible" class="operation-progress-log">
    <el-progress :percentage="percentage" />
    <div class="operation-progress-text">{{ text }}</div>
    <el-alert v-if="summary" :title="summary" type="success" show-icon :closable="false" />
    <el-collapse v-if="logs.length" class="operation-log-collapse">
      <el-collapse-item :title="messages.common.viewDetailedLogs" name="logs">
        <div class="operation-log-panel">
          <p v-for="(item, index) in logs" :key="index">{{ item }}</p>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

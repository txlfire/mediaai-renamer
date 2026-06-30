<script setup lang="ts">
import { computed } from "vue";

import { formatMessage, zhCnMessages as messages } from "../locales/zh-CN";
import {
  PAGE_SIZE_ALL,
  usePaginationStore,
  type PaginationKey,
} from "../stores/pagination";

const props = defineProps<{
  paginationKey: PaginationKey;
  total: number;
}>();

const paginationStore = usePaginationStore();
const pagination = computed(() => paginationStore.getState(props.paginationKey));
</script>

<template>
  <div class="table-pagination">
    <span class="pagination-total">{{ formatMessage(messages.common.totalRows, { total }) }}</span>
    <el-select
      :model-value="pagination.pageSize"
      class="page-size-select"
      size="small"
      @change="(size: number) => paginationStore.setPageSize(paginationKey, size)"
    >
      <el-option :label="messages.common.pageSize10" :value="10" />
      <el-option :label="messages.common.pageSize50" :value="50" />
      <el-option :label="messages.common.all" :value="PAGE_SIZE_ALL" />
    </el-select>
    <el-pagination
      v-if="pagination.pageSize !== PAGE_SIZE_ALL"
      :current-page="pagination.currentPage"
      :page-size="pagination.pageSize"
      :total="total"
      background
      layout="prev, pager, next, jumper"
      @current-change="(page: number) => paginationStore.setPage(paginationKey, page)"
    />
  </div>
</template>

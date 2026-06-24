<script setup lang="ts">
import { computed } from "vue";

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
    <span class="pagination-total">共 {{ total }} 条</span>
    <el-select
      :model-value="pagination.pageSize"
      class="page-size-select"
      size="small"
      @change="(size: number) => paginationStore.setPageSize(paginationKey, size)"
    >
      <el-option label="10 条/页" :value="10" />
      <el-option label="50 条/页" :value="50" />
      <el-option label="全部" :value="PAGE_SIZE_ALL" />
    </el-select>
    <el-pagination
      v-if="pagination.pageSize !== PAGE_SIZE_ALL"
      :current-page="pagination.currentPage"
      :page-size="pagination.pageSize"
      :total="total"
      background
      layout="prev, pager, next"
      @current-change="(page: number) => paginationStore.setPage(paginationKey, page)"
    />
  </div>
</template>

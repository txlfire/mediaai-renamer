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
  pagerCount?: number;
}>();

const paginationStore = usePaginationStore();
const pagination = computed(() => paginationStore.getState(props.paginationKey));
const compactPagerEnabled = computed(() => (props.pagerCount ?? 7) <= 3);
const pageCount = computed(() => {
  if (pagination.value.pageSize === PAGE_SIZE_ALL) {
    return 1;
  }
  return Math.max(1, Math.ceil(props.total / pagination.value.pageSize));
});
const compactPages = computed(() => {
  const totalPages = pageCount.value;
  const currentPage = Math.min(pagination.value.currentPage, totalPages);
  if (totalPages <= 3) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }
  if (currentPage <= 2) {
    return [1, 2, 3];
  }
  if (currentPage >= totalPages - 1) {
    return [totalPages - 2, totalPages - 1, totalPages];
  }
  return [currentPage - 1, currentPage, currentPage + 1];
});
const normalizedPagerCount = computed(() => {
  const count = props.pagerCount ?? 7;
  return count >= 5 ? count : 7;
});

function setCompactPage(page: number) {
  const nextPage = Math.min(Math.max(1, page), pageCount.value);
  paginationStore.setPage(props.paginationKey, nextPage);
}
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
    <div
      v-if="pagination.pageSize !== PAGE_SIZE_ALL && compactPagerEnabled"
      class="compact-pagination"
    >
      <el-button
        size="small"
        :disabled="pagination.currentPage <= 1"
        @click="setCompactPage(pagination.currentPage - 1)"
      >
        &lt;
      </el-button>
      <el-button
        v-for="page in compactPages"
        :key="page"
        size="small"
        :type="page === pagination.currentPage ? 'primary' : 'default'"
        @click="setCompactPage(page)"
      >
        {{ page }}
      </el-button>
      <el-button
        size="small"
        :disabled="pagination.currentPage >= pageCount"
        @click="setCompactPage(pagination.currentPage + 1)"
      >
        &gt;
      </el-button>
      <span class="compact-pagination-jump-label">{{ messages.common.jumpPage }}</span>
      <el-input-number
        :model-value="pagination.currentPage"
        size="small"
        :min="1"
        :max="pageCount"
        :controls="false"
        @change="(page: number | undefined) => setCompactPage(page ?? 1)"
      />
      <span class="compact-pagination-page-label">&#39029;</span>
    </div>
    <el-pagination
      v-if="pagination.pageSize !== PAGE_SIZE_ALL && !compactPagerEnabled"
      :current-page="pagination.currentPage"
      :page-size="pagination.pageSize"
      :pager-count="normalizedPagerCount"
      :total="total"
      background
      layout="prev, pager, next, jumper"
      @current-change="(page: number) => paginationStore.setPage(paginationKey, page)"
    />
  </div>
</template>

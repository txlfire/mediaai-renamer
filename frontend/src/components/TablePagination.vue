<script setup lang="ts">
import { computed } from "vue";
import { ArrowLeft, ArrowRight, DArrowLeft, DArrowRight } from "@element-plus/icons-vue";

import { useInterfaceLanguage } from "../locales/interfaceLanguage";
import { formatMessage } from "../locales/zh-CN";
import { formatDisplayCount } from "../utils/displayFormat";
import {
  PAGE_SIZE_ALL,
  usePaginationStore,
  type PaginationKey,
} from "../stores/pagination";

const props = defineProps<{
  paginationKey?: PaginationKey;
  total?: number;
  currentPage?: number;
  pageSize?: number;
  pageSizeOptions?: readonly number[];
  fixedPageSize?: number;
  sequential?: { hasPrevious: boolean; hasNext: boolean };
  disabled?: boolean;
  pagerCount?: number;
  server?: boolean;
}>();
const emit = defineEmits<{
  (event: "page-change", page: number): void;
  (event: "page-size-change", pageSize: number): void;
  (event: "previous-page"): void;
  (event: "next-page"): void;
}>();

const paginationStore = props.paginationKey ? usePaginationStore() : undefined;
const { messages } = useInterfaceLanguage();
const pagination = computed(() => props.paginationKey && paginationStore
  ? paginationStore.getState(props.paginationKey)
  : { currentPage: props.currentPage ?? 1, pageSize: props.pageSize ?? props.fixedPageSize ?? 50 });
const selectablePageSizes = computed(() => props.pageSizeOptions ?? (props.paginationKey && !props.sequential ? [10, 50] : []));
const allowAllPageSize = computed(() => !!props.paginationKey && !props.sequential && !props.server);
const compactPagerEnabled = computed(() => (props.pagerCount ?? 7) <= 3);
const pageCount = computed(() => {
  if (pagination.value.pageSize === PAGE_SIZE_ALL) {
    return 1;
  }
  return Math.max(1, Math.ceil((props.total ?? 0) / pagination.value.pageSize));
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
  if (props.disabled) return;
  const nextPage = Math.min(Math.max(1, page), pageCount.value);
  if (nextPage === pagination.value.currentPage) return;
  if (props.paginationKey) paginationStore?.setPage(props.paginationKey, nextPage);
  if (!props.paginationKey || props.server) {
    emit("page-change", nextPage);
  }
}

function setPageSize(size: number) {
  if (props.disabled || size === pagination.value.pageSize
    || (!selectablePageSizes.value.includes(size) && !(allowAllPageSize.value && size === PAGE_SIZE_ALL))) return;
  if (props.paginationKey && paginationStore) paginationStore.setPageSize(props.paginationKey, size);
  if (!props.paginationKey || props.server) emit("page-size-change", props.paginationKey ? pagination.value.pageSize : size);
}
</script>

<template>
  <div class="table-pagination">
    <div v-if="$slots.actions" class="table-pagination-actions"><slot name="actions" /></div>
    <span v-if="$slots.summary || !sequential" class="pagination-total">
      <slot name="summary">{{ formatMessage(messages.common.totalRows, { total: formatDisplayCount(total ?? 0) }) }}</slot>
    </span>
    <el-select
      v-if="selectablePageSizes.length"
      :model-value="pagination.pageSize"
      :aria-label="messages.common.pageSizeLabel"
      class="page-size-select"
      size="small"
      :disabled="disabled"
      @change="setPageSize"
    >
      <el-option v-for="size in selectablePageSizes" :key="size" :label="formatMessage(messages.common.fixedPageSize, { size })" :value="size" />
      <el-option v-if="allowAllPageSize" :label="messages.common.all" :value="PAGE_SIZE_ALL" />
    </el-select>
    <span v-else-if="fixedPageSize" class="table-pagination-fixed-size">
      {{ formatMessage(messages.common.fixedPageSize, { size: fixedPageSize }) }}
    </span>
    <div v-if="sequential" class="standard-pagination">
      <el-button class="table-pagination-step" size="small" :icon="ArrowLeft" :disabled="disabled || !sequential.hasPrevious"
        :aria-label="messages.common.previousPage" @click="emit('previous-page')" />
      <span class="table-pagination-page" aria-current="page"
        :aria-label="formatMessage(messages.common.pageNumber, { page: pagination.currentPage })">{{ pagination.currentPage }}</span>
      <el-button class="table-pagination-step" size="small" :icon="ArrowRight" :disabled="disabled || !sequential.hasNext"
        :aria-label="messages.common.nextPage" @click="emit('next-page')" />
    </div>
    <div
      v-if="!sequential && pagination.pageSize !== PAGE_SIZE_ALL && compactPagerEnabled"
      class="compact-pagination"
    >
      <el-tooltip :trigger-keys="[]" :content="messages.common.firstPage" placement="top">
        <el-button
          size="small"
          :disabled="disabled || pagination.currentPage <= 1"
          @click="setCompactPage(1)"
        >
          &laquo;
        </el-button>
      </el-tooltip>
      <el-button
        size="small"
        :disabled="disabled || pagination.currentPage <= 1"
        @click="setCompactPage(pagination.currentPage - 1)"
      >
        &lt;
      </el-button>
      <el-button
        v-for="page in compactPages"
        :key="page"
        size="small"
        :disabled="disabled"
        :type="page === pagination.currentPage ? 'primary' : 'default'"
        @click="setCompactPage(page)"
      >
        {{ page }}
      </el-button>
      <el-button
        size="small"
        :disabled="disabled || pagination.currentPage >= pageCount"
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
        :disabled="disabled"
        @change="(page: number | undefined) => setCompactPage(page ?? 1)"
      />
      <span v-if="messages.common.compactPageSuffix" class="compact-pagination-page-label">{{ messages.common.compactPageSuffix }}</span>
      <el-tooltip :trigger-keys="[]" :content="messages.common.lastPage" placement="top">
        <el-button
          size="small"
          :disabled="disabled || pagination.currentPage >= pageCount"
          @click="setCompactPage(pageCount)"
        >
          &raquo;
        </el-button>
      </el-tooltip>
    </div>
    <div
      v-if="!sequential && pagination.pageSize !== PAGE_SIZE_ALL && !compactPagerEnabled"
      class="standard-pagination"
    >
      <el-tooltip :trigger-keys="[]" :content="messages.common.firstPage" placement="top">
        <el-button
          class="table-pagination-step"
          size="small"
          :icon="DArrowLeft"
          :aria-label="messages.common.firstPage"
          :disabled="disabled || pagination.currentPage <= 1"
          @click="setCompactPage(1)"
        />
      </el-tooltip>
      <el-pagination
        :current-page="pagination.currentPage"
        :page-size="pagination.pageSize"
        :pager-count="normalizedPagerCount"
        :total="total ?? 0"
        :disabled="disabled"
        background
        layout="prev, pager, next, jumper"
        @update:current-page="setCompactPage"
      />
      <el-tooltip :trigger-keys="[]" :content="messages.common.lastPage" placement="top">
        <el-button
          class="table-pagination-step"
          size="small"
          :icon="DArrowRight"
          :aria-label="messages.common.lastPage"
          :disabled="disabled || pagination.currentPage >= pageCount"
          @click="setCompactPage(pageCount)"
        />
      </el-tooltip>
    </div>
  </div>
</template>

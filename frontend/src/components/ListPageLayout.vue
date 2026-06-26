<script setup lang="ts">
import { ArrowDown, ArrowUp, MoreFilled } from "@element-plus/icons-vue";
import { computed, ref, useSlots } from "vue";

import { zhCnMessages as messages } from "../locales/zh-CN";

const props = withDefaults(
  defineProps<{
    title: string;
    description: string;
    collapsibleFilters?: boolean;
    defaultFiltersExpanded?: boolean;
    titleCardMode?: "standalone" | "merged";
  }>(),
  {
    collapsibleFilters: false,
    defaultFiltersExpanded: false,
    titleCardMode: "standalone",
  },
);

const slots = useSlots();
const filtersExpanded = ref(props.defaultFiltersExpanded);
const hasStats = computed(() => Boolean(slots.stats));
const hasFilters = computed(() => Boolean(slots.filters || slots.queryAction || slots.filterActions));
const hasSummary = computed(() => Boolean(slots.stats || slots.actions || slots.moreActions));
const hasMoreActions = computed(() => Boolean(slots.moreActions));
const hasTable = computed(() => Boolean(slots.table || slots.pagination));

function toggleFilters() {
  filtersExpanded.value = !filtersExpanded.value;
}
</script>

<template>
  <section class="workspace-page list-page">
    <el-card class="list-title-card" :class="{ 'is-merged': titleCardMode === 'merged' }" shadow="never">
      <div class="list-title-card-line">
        <h1>{{ title }}</h1>
        <p>{{ description }}</p>
      </div>

      <div v-if="titleCardMode === 'merged' && hasFilters" class="list-filter-row">
        <div class="list-filter-primary">
          <slot name="filters" />
        </div>
        <el-button
          v-if="collapsibleFilters"
          class="list-filter-toggle"
          :icon="filtersExpanded ? ArrowUp : ArrowDown"
          @click="toggleFilters"
        >
          {{ filtersExpanded ? messages.common.collapse : messages.common.expand }}
        </el-button>
        <slot name="queryAction" />
        <div class="list-filter-actions">
          <slot name="filterActions" />
        </div>
      </div>
    </el-card>

    <div v-if="titleCardMode !== 'merged' && hasFilters" class="list-filter-row">
      <div class="list-filter-primary">
        <slot name="filters" />
      </div>
      <el-button
        v-if="collapsibleFilters"
        class="list-filter-toggle"
        :icon="filtersExpanded ? ArrowUp : ArrowDown"
        @click="toggleFilters"
      >
        {{ filtersExpanded ? messages.common.collapse : messages.common.expand }}
      </el-button>
      <slot name="queryAction" />
      <div class="list-filter-actions">
        <slot name="filterActions" />
      </div>
    </div>

    <div v-if="collapsibleFilters && filtersExpanded" class="list-filter-extra">
      <slot name="extraFilters" />
    </div>

    <div v-if="hasSummary" class="list-summary-row" :class="{ 'is-actions-only': !hasStats }">
      <div v-if="hasStats" class="list-stats">
        <slot name="stats" />
      </div>
      <div class="list-actions">
        <slot name="actions" />
        <el-dropdown v-if="hasMoreActions" trigger="click">
          <el-button :icon="MoreFilled">{{ messages.common.more }}</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <slot name="moreActions" />
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <slot />

    <el-card v-if="hasTable" class="list-table-card" shadow="never">
      <slot name="table" />
      <slot name="pagination" />
    </el-card>
  </section>
</template>

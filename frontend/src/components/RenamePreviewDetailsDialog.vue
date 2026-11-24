<script setup lang="ts">
import { computed } from "vue";
import { useInterfaceLanguage } from "../locales/interfaceLanguage";

export type RenamePreviewDetailField = {
  key: string;
  label: string;
  value: string | number | null | undefined;
};

const props = defineProps<{
  visible: boolean;
  fields: readonly RenamePreviewDetailField[];
}>();
const emit = defineEmits<{ "update:visible": [visible: boolean] }>();
const { messages } = useInterfaceLanguage();
const nameKeys = new Set(["originalName", "targetName"]);
const wideKeys = new Set(["sourcePath", "reason", "titleConflict"]);
const names = computed(() => props.fields.filter(field => nameKeys.has(field.key)));
const details = computed(() => props.fields.filter(field => !nameKeys.has(field.key)));
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="messages.renamePreviews.detailTitle"
    width="min(760px, calc(100vw - 24px))"
    align-center
    destroy-on-close
    @update:model-value="emit('update:visible', $event)"
  >
    <dl v-if="names.length" class="rename-detail-fields rename-detail-names">
      <div v-for="field in names" :key="field.key" class="rename-detail-field">
        <dt>{{ field.label }}</dt>
        <dd>{{ field.value ?? "-" }}</dd>
      </div>
    </dl>
    <dl v-if="details.length" class="rename-detail-fields">
      <div
        v-for="field in details"
        :key="field.key"
        class="rename-detail-field"
        :class="{ 'is-wide': wideKeys.has(field.key) }"
      >
        <dt>{{ field.label }}</dt>
        <dd>{{ field.value ?? "-" }}</dd>
      </div>
    </dl>
    <template #footer>
      <el-button @click="emit('update:visible', false)">{{ messages.common.close }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.rename-detail-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 17rem), 1fr));
  gap: 12px 16px;
  margin: 0;
}

.rename-detail-names {
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

.rename-detail-field {
  min-width: 0;
  overflow-wrap: anywhere;
}

.rename-detail-field.is-wide {
  grid-column: 1 / -1;
}

.rename-detail-field dt {
  margin: 0 0 4px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.rename-detail-field dd {
  margin: 0;
  color: var(--el-text-color-primary);
  white-space: pre-wrap;
}

.rename-detail-names dd {
  font-weight: 600;
}
</style>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import Sortable from "sortablejs";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { zhCnMessages as messages } from "../locales/zh-CN";
import {
  buildNamingPreview,
  buildNamingElementPreview,
  createNamingElement,
  detectNamingSemanticWarnings,
  elementHasError,
  elementLabel,
  elementsFromPreset,
  NAMING_ELEMENT_DEFINITIONS,
  NAMING_TEMPLATE_PRESETS,
  type NamingElementFormat,
  type NamingElementDefinition,
  type NamingTemplateElement,
  type NamingTemplateType,
  validateNamingSeparator,
  validateNamingElements,
} from "../utils/namingBuilder";

const text = messages.settings.naming;

const props = defineProps<{
  movieElements: NamingTemplateElement[];
  episodeElements: NamingTemplateElement[];
  separator: string;
  loading?: boolean;
}>();

const emit = defineEmits<{
  "update:movieElements": [value: NamingTemplateElement[]];
  "update:episodeElements": [value: NamingTemplateElement[]];
  "update:separator": [value: string];
  refresh: [];
  save: [];
}>();

const activeType = ref<NamingTemplateType>("movie");
const selectedPresets = reactive<Record<NamingTemplateType, string>>({
  movie: "standard",
  episode: "standard",
});
const builderRef = ref<HTMLElement | null>(null);
const editingIndex = ref<number | null>(null);
const editingValue = ref("");
const formatPopoverIndex = ref<number | null>(null);
const contextMenu = reactive({
  visible: false,
  x: 0,
  y: 0,
  index: -1,
});
let sortable: Sortable | null = null;

const currentElements = computed(() => (activeType.value === "movie" ? props.movieElements : props.episodeElements));
const currentErrors = computed(() => validateNamingElements(currentElements.value, text));
const currentWarnings = computed(() => detectNamingSemanticWarnings(currentElements.value, text));
const currentPreview = computed(() => buildNamingPreview(currentElements.value, props.separator, text, activeType.value));
const separatorError = computed(() => validateNamingSeparator(props.separator, text));
const selectedPreset = computed({
  get: () => selectedPresets[activeType.value],
  set: (value: string) => {
    selectedPresets[activeType.value] = value || "standard";
  },
});
const presetOptions = computed(() =>
  Object.keys(NAMING_TEMPLATE_PRESETS[activeType.value]).map((value) => ({
    value,
    label: presetLabel(value),
  })),
);
const bracketStyleOptions: Array<{ label: string; value: NonNullable<NamingElementFormat["bracketStyle"]> }> = [
  { label: text.bracketStyles.none, value: "none" },
  { label: text.bracketStyles.square, value: "square" },
  { label: text.bracketStyles.round, value: "round" },
  { label: text.bracketStyles.curly, value: "curly" },
];

const elementGroups = computed(() => {
  return Object.entries(
    NAMING_ELEMENT_DEFINITIONS.reduce<Record<string, NamingElementDefinition[]>>((groups, item) => {
      groups[item.category] = groups[item.category] || [];
      groups[item.category].push(item);
      return groups;
    }, {}),
  ).map(([key, items]) => ({ key, label: text.categories[key as keyof typeof text.categories] || key, items }));
});

function presetLabel(value: string) {
  return text.presets[value as keyof typeof text.presets] || value;
}

function emitElements(elements: NamingTemplateElement[]) {
  if (activeType.value === "movie") {
    emit("update:movieElements", elements);
  } else {
    emit("update:episodeElements", elements);
  }
}

function cloneElements(elements: NamingTemplateElement[]) {
  return elements.map((element) => ({
    ...element,
    format: element.format ? { ...element.format } : undefined,
  }));
}

function baseVariable(variable: string) {
  return variable.split(":")[0];
}

function addElement(definition: NamingElementDefinition) {
  const duplicate = currentElements.value.some((element) => baseVariable(element.variable) === definition.variable);
  if (duplicate) {
    ElMessage.warning(text.duplicateElement);
    return;
  }
  emitElements([...cloneElements(currentElements.value), createNamingElement(definition.key, text)]);
}

async function applyPreset(value: string) {
  if (!value) {
    return;
  }
  try {
    await ElMessageBox.confirm(text.presetConfirm, text.presetConfirmTitle, {
      confirmButtonText: messages.common.confirm,
      cancelButtonText: messages.common.cancel,
      type: "warning",
    });
    emitElements(elementsFromPreset(activeType.value, value, text));
  } catch {
    selectedPreset.value = "standard";
  }
}

function resetCurrentPreset() {
  emitElements(elementsFromPreset(activeType.value, selectedPreset.value || "standard", text));
}

function removeElement(index: number) {
  emitElements(currentElements.value.filter((_, itemIndex) => itemIndex !== index));
  closeContextMenu();
}

function moveElement(index: number, targetIndex: number) {
  const elements = cloneElements(currentElements.value);
  const [item] = elements.splice(index, 1);
  elements.splice(Math.max(0, Math.min(targetIndex, elements.length)), 0, item);
  emitElements(elements);
  closeContextMenu();
}

function moveToStart(index: number) {
  moveElement(index, 0);
}

function moveToEnd(index: number) {
  moveElement(index, currentElements.value.length - 1);
}

function startEditing(index: number) {
  editingIndex.value = index;
  editingValue.value = currentElements.value[index]?.variable || "";
}

function commitEditing() {
  if (editingIndex.value === null) {
    return;
  }
  const elements = cloneElements(currentElements.value);
  const index = editingIndex.value;
  elements[index] = { ...elements[index], variable: editingValue.value.trim() || elements[index].variable };
  editingIndex.value = null;
  editingValue.value = "";
  emitElements(elements);
}

function updateElementVariable(index: number, variable: string) {
  const elements = cloneElements(currentElements.value);
  if (!elements[index]) {
    return;
  }
  elements[index] = { ...elements[index], variable: variable.trim() || elements[index].variable };
  emitElements(elements);
}

function updateElementFormat(
  index: number,
  patch: { pad?: number; prefix?: string; customText?: string; bracketStyle?: NamingElementFormat["bracketStyle"] },
) {
  const elements = cloneElements(currentElements.value);
  const element = elements[index];
  if (!element) {
    return;
  }
  elements[index] = {
    ...element,
    customText: patch.customText ?? element.customText,
    format: {
      ...(element.format || {}),
      ...(patch.pad !== undefined ? { pad: patch.pad } : {}),
      ...(patch.prefix !== undefined ? { prefix: patch.prefix } : {}),
      ...(patch.bracketStyle !== undefined ? { bracketStyle: patch.bracketStyle } : {}),
    },
  };
  emitElements(elements);
}

function updateBracketStyle(index: number, value: string) {
  updateElementFormat(index, { bracketStyle: value as NamingElementFormat["bracketStyle"] });
}

function elementFormatPreview(element: NamingTemplateElement) {
  return buildNamingElementPreview(element, text);
}

function showContextMenu(event: MouseEvent, index: number) {
  event.preventDefault();
  contextMenu.visible = true;
  contextMenu.x = event.clientX;
  contextMenu.y = event.clientY;
  contextMenu.index = index;
}

function closeContextMenu() {
  contextMenu.visible = false;
  contextMenu.index = -1;
}

function syncSortable() {
  sortable?.destroy();
  sortable = null;
  if (!builderRef.value) {
    return;
  }
  sortable = Sortable.create(builderRef.value, {
    animation: 150,
    draggable: ".naming-builder-token",
    filter: ".naming-builder-token-remove, .naming-builder-token-input",
    ghostClass: "is-dragging",
    onEnd(event: Sortable.SortableEvent) {
      if (event.oldIndex === undefined || event.newIndex === undefined || event.oldIndex === event.newIndex) {
        return;
      }
      moveElement(event.oldIndex, event.newIndex);
    },
  });
}

watch(activeType, () => {
  closeContextMenu();
  nextTick(syncSortable);
});

watch(
  () => currentElements.value.length,
  () => nextTick(syncSortable),
);

onMounted(() => {
  document.addEventListener("click", closeContextMenu);
  syncSortable();
});

onBeforeUnmount(() => {
  document.removeEventListener("click", closeContextMenu);
  sortable?.destroy();
});
</script>

<template>
  <div class="naming-builder">
    <div class="naming-builder-toolbar">
      <span class="naming-builder-label">{{ text.applicableType }}</span>
      <el-segmented
        v-model="activeType"
        :options="[{ label: text.movie, value: 'movie' }, { label: text.episode, value: 'episode' }]"
      />
      <el-select v-model="selectedPreset" class="naming-preset-select" :placeholder="text.presetPlaceholder" @change="applyPreset">
        <el-option
          v-for="preset in presetOptions"
          :key="preset.value"
          :label="preset.label"
          :value="preset.value"
        />
      </el-select>
      <div class="naming-builder-actions">
        <el-button :loading="loading" @click="resetCurrentPreset">{{ text.resetCurrentPreset }}</el-button>
        <el-button :loading="loading" @click="emit('refresh')">{{ messages.common.refresh }}</el-button>
        <el-button type="primary" :loading="loading" @click="emit('save')">{{ messages.common.save }}</el-button>
      </div>
    </div>

    <div class="naming-builder-section">
      <div class="naming-builder-section-title">{{ text.builderTitle }}</div>
      <div ref="builderRef" class="naming-builder-dropzone">
        <el-popover
          v-for="(element, index) in currentElements"
          :key="element.id"
          :visible="formatPopoverIndex === index"
          trigger="click"
          placement="bottom-start"
          width="260"
          @hide="formatPopoverIndex = null"
        >
          <template #reference>
            <button
              class="naming-builder-token"
              :class="{ 'is-invalid': elementHasError(currentErrors, index) }"
              type="button"
              @click.stop="formatPopoverIndex = formatPopoverIndex === index ? null : index"
              @dblclick.stop="startEditing(index)"
              @contextmenu="showContextMenu($event, index)"
            >
              <template v-if="editingIndex === index">
                <input
                  v-model="editingValue"
                  class="naming-builder-token-input"
                  @click.stop
                  @keydown.enter.prevent="commitEditing"
                  @keydown.esc.prevent="editingIndex = null"
                  @blur="commitEditing"
                />
              </template>
              <template v-else>
                <span>{{ element.label }}</span>
                <small>{ {{ element.variable }} }</small>
                <span
                  class="naming-builder-token-remove"
                  role="button"
                  :aria-label="text.removeElement"
                  @click.stop="removeElement(index)"
                >
                  ×
                </span>
              </template>
            </button>
          </template>
          <div class="naming-format-popover">
            <label>
              <span>{{ text.variableName }}</span>
              <el-input
                :model-value="element.variable"
                size="small"
                @change="(value: string) => updateElementVariable(index, value)"
              />
            </label>
            <label>
              <span>{{ text.paddingDigits }}</span>
              <el-input-number
                :model-value="element.format?.pad || 0"
                :min="0"
                :max="8"
                size="small"
                controls-position="right"
                @change="(value: number | undefined) => updateElementFormat(index, { pad: Number(value || 0) })"
              />
            </label>
            <label>
              <span>{{ text.prefix }}</span>
              <el-input
                :model-value="element.format?.prefix || ''"
                size="small"
                @change="(value: string) => updateElementFormat(index, { prefix: value })"
              />
            </label>
            <label v-if="element.key === 'custom'">
              <span>{{ text.customText }}</span>
              <el-input
                :model-value="element.customText || ''"
                size="small"
                @change="(value: string) => updateElementFormat(index, { customText: value })"
              />
            </label>
            <label v-if="element.key === 'tmdb_id' || element.key === 'imdb_id'">
              <span>{{ text.bracketStyle }}</span>
              <el-select
                :model-value="element.format?.bracketStyle || 'square'"
                size="small"
                @change="(value: string) => updateBracketStyle(index, value)"
              >
                <el-option
                  v-for="option in bracketStyleOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </label>
            <div v-if="element.key === 'tmdb_id' || element.key === 'imdb_id'" class="naming-format-preview">
              <span>{{ text.formatPreview }}</span>
              <strong>{{ elementFormatPreview(element) }}</strong>
            </div>
          </div>
        </el-popover>
      </div>
      <div v-if="currentErrors.length" class="naming-builder-errors">
        <span v-for="(error, index) in currentErrors" :key="`${error.index}-${index}`">{{ error.message }}</span>
      </div>
    </div>

    <div class="naming-separator-row">
      <label>{{ text.separator }}：</label>
      <el-input
        :model-value="separator"
        class="naming-separator-input"
        :class="{ 'is-invalid': separatorError }"
        maxlength="5"
        @update:model-value="(value: string) => emit('update:separator', value)"
      />
      <span v-if="separatorError" class="naming-separator-error">{{ separatorError }}</span>
    </div>

    <div class="naming-preview-line">
      <strong>{{ text.livePreview }}</strong>
      <span>{{ currentPreview }}</span>
    </div>
    <div v-if="currentWarnings.length" class="naming-builder-warnings">
      <span v-for="warning in currentWarnings" :key="warning">{{ warning }}</span>
    </div>

    <div class="naming-element-library">
      <div class="naming-builder-section-title">{{ text.availableElements }}</div>
      <div v-for="group in elementGroups" :key="group.key" class="naming-element-group">
        <span class="naming-element-group-label">{{ group.label }}：</span>
        <button
          v-for="item in group.items"
          :key="item.key"
          class="naming-element-button"
          type="button"
          @click="addElement(item)"
        >
          {{ elementLabel(item, text) }}
        </button>
      </div>
    </div>

    <div
      v-if="contextMenu.visible"
      class="naming-context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      @click.stop
    >
      <button type="button" @click="removeElement(contextMenu.index)">{{ text.removeElement }}</button>
      <button type="button" @click="moveElement(contextMenu.index, contextMenu.index - 1)">{{ text.moveUp }}</button>
      <button type="button" @click="moveElement(contextMenu.index, contextMenu.index + 1)">{{ text.moveDown }}</button>
      <button type="button" @click="moveToStart(contextMenu.index)">{{ text.moveToStart }}</button>
      <button type="button" @click="moveToEnd(contextMenu.index)">{{ text.moveToEnd }}</button>
    </div>
  </div>
</template>

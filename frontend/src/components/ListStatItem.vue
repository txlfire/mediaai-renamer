<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string;
    value: number | string;
    tone?: "default" | "success" | "warning" | "edited" | "renamed" | "danger";
  }>(),
  {
    tone: "default",
  },
);
</script>

<template>
  <div class="list-stat-item" :class="`is-${tone}`">
    <span>{{ label }}</span>
    <strong>{{ value }}</strong>
  </div>
</template>

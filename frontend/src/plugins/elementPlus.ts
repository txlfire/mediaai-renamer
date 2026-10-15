import type { App } from "vue";
import {
  ElAlert, ElButton, ElCard, ElCheckbox, ElCheckboxGroup, ElCollapse, ElCollapseItem,
  ElDatePicker, ElDescriptions, ElDescriptionsItem, ElDialog, ElDivider, ElDrawer,
  ElDropdown, ElDropdownItem, ElDropdownMenu, ElEmpty, ElForm, ElFormItem, ElIcon,
  ElInput, ElInputNumber, ElLoading, ElMessage, ElMessageBox, ElOption, ElPagination, ElPopover, ElProgress,
  ElRadio, ElRadioButton, ElRadioGroup, ElSegmented, ElSelect, ElSwitch, ElTabPane, ElTable,
  ElTableColumn, ElTabs, ElTag, ElTooltip, provideGlobalConfig,
} from "element-plus";

// 显式注册实际使用的组件；测试核对Vue模板，避免新增组件后漏注册。
export const elementPlusComponents = {
  ElAlert, ElButton, ElCard, ElCheckbox, ElCheckboxGroup, ElCollapse, ElCollapseItem,
  ElDatePicker, ElDescriptions, ElDescriptionsItem, ElDialog, ElDivider, ElDrawer,
  ElDropdown, ElDropdownItem, ElDropdownMenu, ElEmpty, ElForm, ElFormItem, ElIcon,
  ElInput, ElInputNumber, ElOption, ElPagination, ElPopover, ElProgress, ElRadio, ElRadioButton,
  ElRadioGroup, ElSegmented, ElSelect, ElSwitch, ElTabPane, ElTable, ElTableColumn,
  ElTabs, ElTag, ElTooltip,
};

export function installElementPlus(app: App, config: Parameters<typeof provideGlobalConfig>[0]) {
  provideGlobalConfig(config, app, true);
  Object.entries(elementPlusComponents).forEach(([name, component]) => app.component(name, component));
  app.use(ElLoading);
  app.use(ElMessage);
  app.use(ElMessageBox);
}

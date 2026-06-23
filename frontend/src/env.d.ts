/**
 * 前端类型声明文件。
 *
 * 让 TypeScript 能识别 Vue 单文件组件和 CSS 侧效导入。
 */

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<object, object, unknown>;
  export default component;
}

declare module "*.css";

/// <reference types="vite/client" />
import { parse as parseSfc } from "@vue/compiler-sfc";
import { parse as parseTemplate, NodeTypes } from "@vue/compiler-dom";
import { createApp } from "vue";
import { describe, expect, it } from "vitest";
import { elementPlusComponents, installElementPlus } from "./elementPlus";

describe("explicit Element Plus registration", () => {
  it("registers every Element Plus component used by the Vue templates", () => {
    const registered = new Set(Object.keys(elementPlusComponents).map(name => name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`).slice(1)));
    const used = new Set<string>();
    const sources = import.meta.glob<string>('../**/*.vue', { query: '?raw', import: 'default', eager: true });
    for (const source of Object.values(sources)) {
      const template = parseSfc(source).descriptor.template;
      if (!template) continue;
      const walk = (node: ReturnType<typeof parseTemplate> | ReturnType<typeof parseTemplate>['children'][number]) => {
        if (node.type === NodeTypes.ELEMENT && node.tag.startsWith('el-')) used.add(node.tag);
        if (node.type === NodeTypes.ROOT || node.type === NodeTypes.ELEMENT) node.children.forEach(walk);
      };
      walk(parseTemplate(template.content));
    }
    expect(used.size).toBeGreaterThan(30);
    expect([...used].filter(tag => !registered.has(tag))).toEqual([]);
  });
  it("keeps the loading directive and global configuration installed", () => {
    const app = createApp({ render: () => null });
    installElementPlus(app, { locale: { name: 'zh-cn', el: { table: { emptyText: '暂无匹配数据' } } } });
    expect(app.directive('loading')).toBeDefined();
    expect(app.config.globalProperties.$message).toBeDefined();
    expect(app.config.globalProperties.$msgbox).toBeDefined();
    expect(app.component('ElTable')).toBe(elementPlusComponents.ElTable);
    expect(Object.keys(app._context.provides).length + Object.getOwnPropertySymbols(app._context.provides).length).toBeGreaterThan(0);
  });
});

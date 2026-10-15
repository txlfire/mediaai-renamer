import { NodeTypes, baseParse, type RootNode, type TemplateChildNode } from "@vue/compiler-dom";
import { parse } from "@vue/compiler-sfc";
import { describe, expect, it } from "vitest";
import appSource from "../App.vue?raw";
import namingSource from "./NamingTemplateBuilder.vue?raw";
import textCellSource from "./TextCell.vue?raw";

const sources = import.meta.glob<string>("../**/*.vue", { query: "?raw", import: "default", eager: true });

function elements(node: RootNode | TemplateChildNode): Extract<TemplateChildNode, { type: NodeTypes.ELEMENT }>[] {
  const children = node.type === NodeTypes.ROOT || node.type === NodeTypes.ELEMENT ? node.children : [];
  return [...(node.type === NodeTypes.ELEMENT ? [node] : []), ...children.flatMap(child => elements(child))];
}

describe("原生按钮键盘契约", () => {
  it("截断文字支持焦点提示，短文字不增加Tab停靠点", () => {
    const nodes = elements(baseParse(parse(textCellSource).descriptor.template!.content));
    const tooltip = nodes.find(node => node.tag === "el-tooltip")!;
    const text = nodes.find(node => node.tag === "span" && node.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "class" && prop.value?.content === "text-cell"))!;
    const expression = (node: typeof tooltip, name: string) => {
      const prop = node.props.find(item => item.type === NodeTypes.DIRECTIVE && item.arg?.type === NodeTypes.SIMPLE_EXPRESSION && item.arg.content === name);
      return prop?.type === NodeTypes.DIRECTIVE && prop.exp?.type === NodeTypes.SIMPLE_EXPRESSION ? prop.exp.content : undefined;
    };
    expect(expression(tooltip, "trigger")).toBe("['hover', 'focus']");
    expect(expression(text, "tabindex")).toBe("needsTooltip && focusable ? 0 : undefined");
  });

  it("说明型Tooltip不抢占Enter/Space，业务按钮保留原生激活", () => {
    let count = 0;
    for (const [file, source] of Object.entries(sources)) {
      const template = parse(source).descriptor.template;
      if (!template) continue;
      for (const element of elements(baseParse(template.content)).filter(node => node.tag === "el-tooltip")) {
        const triggerKeys = element.props.find(prop => prop.type === NodeTypes.DIRECTIVE && prop.name === "bind" && prop.arg?.type === NodeTypes.SIMPLE_EXPRESSION && prop.arg.content === "trigger-keys");
        expect(triggerKeys?.type === NodeTypes.DIRECTIVE && triggerKeys.exp?.type === NodeTypes.SIMPLE_EXPRESSION ? triggerKeys.exp.content : undefined, file).toBe("[]");
        count++;
      }
    }
    expect(count).toBeGreaterThan(40);
  });

  it("折叠导航和退出按钮不依赖可见文字或悬停才有名称", () => {
    const template = parse(appSource).descriptor.template!;
    const nodes = elements(baseParse(template.content));
    for (const node of nodes.filter(element => element.tag === "RouterLink" || element.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "class" && prop.value?.content === "logout-button"))) {
      expect(node.props.some(prop => prop.type === NodeTypes.DIRECTIVE && prop.arg?.type === NodeTypes.SIMPLE_EXPRESSION && prop.arg.content === "aria-label")).toBe(true);
    }
  });

  it("命名元素使用独立原生按钮，不嵌套交互控件", () => {
    const template = parse(namingSource).descriptor.template!;
    const nodes = elements(baseParse(template.content));
    const token = nodes.find(node => node.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "class" && prop.value?.content === "naming-builder-token"));
    expect(token?.tag).toBe("div");
    for (const name of ["naming-builder-token-main", "naming-builder-token-remove"]) {
      const button = nodes.find(node => node.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "class" && prop.value?.content === name));
      expect(button?.tag).toBe("button");
      expect(button?.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "type" && prop.value?.content === "button")).toBe(true);
    }
  });
});

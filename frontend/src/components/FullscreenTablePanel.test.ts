/// <reference types="vite/client" />
import { parse as parseTemplate, NodeTypes } from "@vue/compiler-dom";
import { parse } from "@vue/compiler-sfc";
import { describe, expect, it } from "vitest";
import source from "./FullscreenTablePanel.vue?raw";
import appSource from "../App.vue?raw";

describe("全屏表格挂载契约", () => {
  it("同一轮创建的工作区目标延后解析，不重建业务插槽", () => {
    const template = parse(source).descriptor.template;
    expect(template).not.toBeNull();
    const root = parseTemplate(template!.content);
    const teleport = root.children.find(node => node.type === NodeTypes.ELEMENT && node.tag === "Teleport");
    if (!teleport || teleport.type !== NodeTypes.ELEMENT) throw new Error("缺少表格Teleport");
    expect(teleport.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "defer")).toBe(true);
    expect(teleport.props.some(prop => prop.type === NodeTypes.DIRECTIVE && prop.name === "bind"
      && prop.arg?.type === NodeTypes.SIMPLE_EXPRESSION && prop.arg.content === "to"
      && prop.exp?.type === NodeTypes.SIMPLE_EXPRESSION && prop.exp.content.includes("workbenchHost"))).toBe(true);
    expect(teleport.props.some(prop => prop.type === NodeTypes.DIRECTIVE && prop.name === "if")).toBe(false);
    expect(source).not.toContain("'.app-workbench'");
    expect(appSource).toMatch(/<RouterView\s*\/>\s*<div class="workbench-fullscreen-host"\s*\/>/);
  });
});

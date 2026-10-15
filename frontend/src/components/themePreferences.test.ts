import { baseParse, NodeTypes, type RootNode, type TemplateChildNode } from "@vue/compiler-dom";
import { parse } from "@vue/compiler-sfc";
import postcss from "postcss";
import cssSource from "../styles.css?raw";
import { describe, expect, it } from "vitest";
import { themeDefinitions } from "../config/themeRegistry";
import optionsSource from "./ThemePreferenceOptions.vue?raw";
import preferencesSource from "./ThemePreferences.vue?raw";

function elements(node: RootNode | TemplateChildNode): Extract<TemplateChildNode, { type: NodeTypes.ELEMENT }>[] {
  const children = node.type === NodeTypes.ROOT || node.type === NodeTypes.ELEMENT ? node.children : [];
  return [...(node.type === NodeTypes.ELEMENT ? [node] : []), ...children.flatMap(elements)];
}

describe("外观结构契约（浏览器交互另验）", () => {
  it("实际浅标签背景混色后的五种状态文字保持可读", () => {
    const css = postcss.parse(cssSource);
    const rgb = (hex: string) => {
      expect(hex).toMatch(/^#[\da-f]{6}$/i);
      return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255);
    };
    const luminance = (values: number[]) => values.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    for (const theme of themeDefinitions) for (const scheme of ["light", "dark"]) {
      const values: Record<string, string> = {};
      css.walkRules(rule => {
        if (rule.selector.includes(`.theme-preview[data-preview-palette="${theme.id}"]`)) rule.walkDecls(decl => { values[decl.prop] = decl.value; });
      });
      const panel = rgb(values[`--panel-bg-${scheme}`]!);
      for (const role of ["brand-accent", "semantic-success", "semantic-warning", "semantic-danger", "semantic-info"]) {
        const foreground = rgb(values[`--${role}-${scheme}`]!);
        const background = foreground.map((value, index) => value * .1 + panel[index]! * .9);
        const a = luminance(foreground), b = luminance(background);
        expect((Math.max(a, b) + .05) / (Math.min(a, b) + .05), `${theme.id}/${scheme}/${role}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("每套预览和根主题声明相同的完整基础角色，不复制组件分支", () => {
    const css = postcss.parse(cssSource);
    const sets = themeDefinitions.map(theme => {
      const selector = `${theme.id === "default" ? ":root" : `:root[data-palette="${theme.id}"]`}, .theme-preview[data-preview-palette="${theme.id}"]`;
      const rule = css.nodes.find(node => node.type === "rule" && node.selector === selector);
      if (!rule || rule.type !== "rule") throw new Error(`缺少主题${theme.id}`);
      const props: string[] = [];
      rule.walkDecls(decl => { props.push(decl.prop); });
      expect(props).toContain("--sidebar-bg-dark");
      expect(props).toContain("--selection-ink-light");
      expect(props).toContain("--action-delete-dark-hover");
      expect(props.some(prop => prop.startsWith("--el-") || prop === "--panel-bg")).toBe(false);
      return props.sort();
    });
    for (const props of sets) expect(props).toEqual(sets[0]);
    css.walkRules(rule => {
      if (rule.selector.includes("data-palette")) expect(rule.selector).not.toMatch(/\s\.el-/);
    });
  });

  it("两种容器复用相同选项组件，使用原生单选且预览不嵌套业务控件", () => {
    const nodes = elements(baseParse(parse(preferencesSource).descriptor.template!.content));
    expect(nodes.filter(node => node.tag === "ThemePreferenceOptions")).toHaveLength(2);
    const optionNodes = elements(baseParse(parse(optionsSource).descriptor.template!.content));
    expect(optionNodes.filter(node => node.tag === "el-radio-group")).toHaveLength(2);
    const list = optionNodes.find(node => node.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.value?.content === "theme-preference-field"));
    expect(list?.tag).toBe("div");
    const preview = optionNodes.find(node => node.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.value?.content === "theme-preview"));
    expect(preview?.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === "aria-hidden" && prop.value?.content === "true")).toBe(true);
    expect(preview && elements(preview).every(node => node.tag === "span" || node.tag === "i")).toBe(true);
    expect(optionsSource).not.toMatch(/\.scrollIntoView\(/);
    expect(optionsSource).toContain("preventScroll: true");
    expect(preferencesSource).toContain("if (isThemePalette(value))");
  });
});

import { describe, expect, it } from "vitest";

import { exportNamingTemplateBundle, importNamingTemplateBundle } from "./namingTemplateWorkbench";

describe("namingTemplateWorkbench", () => {
  it("exports a versioned naming template bundle", () => {
    const exported = exportNamingTemplateBundle({
      movieTemplate: '[{"key":"title","label":"标题","variable":"title"}]',
      episodeTemplate: '[{"key":"title","label":"标题","variable":"title"},{"key":"season_episode","label":"季集组合","variable":"season_episode"}]',
      separator: ".",
      keepYear: true,
    });

    const payload = JSON.parse(exported) as Record<string, unknown>;
    expect(payload.schema_version).toBe(1);
    expect(payload.movie_template).toBeDefined();
    expect(payload.episode_template).toBeDefined();
    expect(payload.separator).toBe(".");
    expect(payload.keep_year).toBe(true);
  });

  it("imports a versioned naming template bundle", () => {
    const imported = importNamingTemplateBundle(
      JSON.stringify({
        schema_version: 1,
        movie_template: '[{"key":"title","label":"标题","variable":"title"}]',
        episode_template: '[{"key":"title","label":"标题","variable":"title"},{"key":"season_episode","label":"季集组合","variable":"season_episode"}]',
        separator: "-",
        keep_year: false,
      }),
    );

    expect(imported.movieTemplate).toContain('"title"');
    expect(imported.episodeTemplate).toContain('"season_episode"');
    expect(imported.separator).toBe("-");
    expect(imported.keepYear).toBe(false);
  });

  it("rejects ambiguous legacy content with a clear error", () => {
    expect(() => importNamingTemplateBundle('[{"key":"title","label":"标题","variable":"title"}]')).toThrow(
      "导入文件缺少模板类型信息",
    );
  });
});

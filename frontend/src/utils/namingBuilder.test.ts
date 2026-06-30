import { describe, expect, it } from "vitest";

import { zhCnMessages as messages } from "../locales/zh-CN";
import {
  buildNamingPreview,
  detectNamingSemanticWarnings,
  elementsFromPreset,
  parseNamingTemplate,
  serializeNamingElements,
  validateNamingSeparator,
  validateNamingElements,
} from "./namingBuilder";

const text = messages.settings.naming;

describe("naming builder utilities", () => {
  it("parses legacy string templates into visual elements", () => {
    const elements = parseNamingTemplate("{title}.{year}.S{season:02d}E{episode:02d}", "episode", text);

    expect(elements.map((element) => element.variable)).toEqual(["title", "year", "season:02d", "episode:02d"]);
  });

  it("restores JSON templates from settings storage", () => {
    const original = elementsFromPreset("movie", "standard", text);
    const restored = parseNamingTemplate(serializeNamingElements(original), "movie", text);

    expect(restored.map((element) => element.key)).toEqual(["title", "year", "resolution", "source"]);
  });

  it("detects duplicate variables regardless of format parameters", () => {
    const elements = parseNamingTemplate("{title}.{season}.{season:02d}", "episode", text);

    expect(validateNamingElements(elements, text).some((error) => error.message === "存在重复元素：{season}")).toBe(true);
  });

  it("requires a title element", () => {
    const elements = parseNamingTemplate("{year}.{resolution}", "movie", text);

    expect(validateNamingElements(elements, text)).toContainEqual({ index: -1, message: text.validation.titleRequired });
  });

  it("builds a sample preview with the selected separator", () => {
    const elements = elementsFromPreset("movie", "standard", text);

    expect(buildNamingPreview(elements, ".", text)).toBe(`${text.sample.title}.2010.1080p.${text.sample.source}.mkv`);
  });

  it("validates separator length and illegal filename characters while allowing spaces", () => {
    expect(validateNamingSeparator(" - ", text)).toBeNull();
    expect(validateNamingSeparator("......", text)).toBe(text.validation.invalidSeparator);
    expect(validateNamingSeparator("/", text)).toBe(text.validation.invalidSeparator);
  });

  it("warns about semantic overlaps without treating them as validation errors", () => {
    const elements = parseNamingTemplate("{title}.{season}.{season_episode}", "episode", text);

    expect(detectNamingSemanticWarnings(elements, text)).toContain(
      "提示：季号与季集组合同时存在，可能导致文件名中季信息重复。建议移除其中一个。",
    );
    expect(validateNamingElements(elements, text)).toEqual([]);
  });

  it("formats TMDB and IMDb ids with configurable bracket styles", () => {
    const [tmdbElement] = parseNamingTemplate(
      JSON.stringify([{ key: "tmdb_id", label: "TMDB ID", variable: "tmdb_id" }]),
      "movie",
      text,
    );
    const [imdbElement] = parseNamingTemplate(
      JSON.stringify([{ key: "imdb_id", label: "IMDb ID", variable: "imdb_id", format: { bracketStyle: "round" } }]),
      "movie",
      text,
    );

    expect(buildNamingPreview([{ ...tmdbElement }, ...elementsFromPreset("movie", "minimal", text)], ".", text)).toContain(
      "[tmdb-27205]",
    );
    expect(buildNamingPreview([{ ...imdbElement }, ...elementsFromPreset("movie", "minimal", text)], ".", text)).toContain(
      "(tt1375666)",
    );
  });
});

export type NamingTemplateBundleInput = {
  movieTemplate: string;
  episodeTemplate: string;
  separator: string;
  keepYear: boolean;
};

export type NamingTemplateBundle = NamingTemplateBundleInput & {
  schemaVersion: number;
};

export function exportNamingTemplateBundle(input: NamingTemplateBundleInput): string {
  return JSON.stringify(
    {
      schema_version: 1,
      movie_template: input.movieTemplate,
      episode_template: input.episodeTemplate,
      separator: input.separator,
      keep_year: input.keepYear,
    },
    null,
    2,
  );
}

export function importNamingTemplateBundle(rawText: string): NamingTemplateBundle {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error("导入文件不是有效的 JSON");
  }

  if (Array.isArray(parsed) || typeof parsed === "string") {
    throw new Error("导入文件缺少模板类型信息");
  }
  if (!parsed || typeof parsed !== "object") {
    throw new Error("导入文件格式不正确");
  }

  const payload = parsed as Record<string, unknown>;
  const schemaVersion = Number(payload.schema_version ?? 1);
  if (!Number.isFinite(schemaVersion) || schemaVersion < 1) {
    throw new Error("导入文件的模板版本不受支持");
  }

  const movieTemplate = String(payload.movie_template || "").trim();
  const episodeTemplate = String(payload.episode_template || "").trim();
  if (!movieTemplate || !episodeTemplate) {
    throw new Error("导入文件缺少电影或剧集模板");
  }

  return {
    schemaVersion,
    movieTemplate,
    episodeTemplate,
    separator: String(payload.separator || "."),
    keepYear: Boolean(payload.keep_year ?? true),
  };
}

export type NamingElementFormat = {
  pad?: number;
  prefix?: string;
  bracketStyle?: "none" | "square" | "round" | "curly";
};

export type NamingTemplateElement = {
  id: string;
  key: string;
  label: string;
  variable: string;
  format?: NamingElementFormat;
  customText?: string;
};

export type NamingTemplateType = "movie" | "episode";

export type NamingElementDefinition = {
  key: string;
  labelKey: string;
  variable: string;
  category: string;
  format?: NamingElementFormat;
};

export type NamingValidationError = {
  index: number;
  message: string;
};

export const VARIABLE_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*(?::[^{}]+)?$/;

export const NAMING_ELEMENT_DEFINITIONS: NamingElementDefinition[] = [
  { key: "title", labelKey: "title", variable: "title", category: "core" },
  { key: "original_title", labelKey: "originalTitle", variable: "original_title", category: "core" },
  { key: "year", labelKey: "year", variable: "year", category: "core" },
  {
    key: "tmdb_id",
    labelKey: "tmdbId",
    variable: "tmdb_id",
    category: "core",
    format: { prefix: "tmdb-", bracketStyle: "square" },
  },
  {
    key: "imdb_id",
    labelKey: "imdbId",
    variable: "imdb_id",
    category: "core",
    format: { prefix: "tt", bracketStyle: "square" },
  },
  { key: "resolution", labelKey: "resolution", variable: "resolution", category: "media" },
  { key: "video_codec", labelKey: "videoCodec", variable: "video_codec", category: "media" },
  { key: "audio_codec", labelKey: "audioCodec", variable: "audio_codec", category: "media" },
  { key: "frame_rate", labelKey: "frameRate", variable: "frame_rate", category: "media" },
  { key: "color_space", labelKey: "colorSpace", variable: "color_space", category: "media" },
  { key: "aspect_ratio", labelKey: "aspectRatio", variable: "aspect_ratio", category: "media" },
  { key: "audio_channels", labelKey: "audioChannels", variable: "audio_channels", category: "media" },
  { key: "source", labelKey: "source", variable: "source", category: "source" },
  { key: "release_group", labelKey: "releaseGroup", variable: "release_group", category: "source" },
  { key: "season", labelKey: "season", variable: "season", category: "episode", format: { pad: 2 } },
  { key: "episode", labelKey: "episode", variable: "episode", category: "episode", format: { pad: 2 } },
  { key: "season_episode", labelKey: "seasonEpisode", variable: "season_episode", category: "episode" },
  { key: "rating", labelKey: "rating", variable: "rating", category: "extra" },
  { key: "subtitle_language", labelKey: "subtitleLanguage", variable: "subtitle_language", category: "extra" },
  { key: "custom", labelKey: "custom", variable: "custom_text", category: "custom" },
];

export const NAMING_TEMPLATE_PRESETS: Record<NamingTemplateType, Record<string, string[]>> = {
  movie: {
    minimal: ["title", "year"],
    standard: ["title", "year", "resolution", "source"],
    full: ["title", "year", "resolution", "source", "video_codec", "imdb_id"],
    pt: ["title", "year", "resolution", "source", "video_codec", "audio_codec", "release_group"],
  },
  episode: {
    minimal: ["title", "season_episode"],
    standard: ["title", "year", "season_episode", "resolution"],
    full: ["title", "year", "season_episode", "resolution", "source", "video_codec"],
  },
};

export const DEFAULT_NAMING_SAMPLE_DATA: Record<string, string | number> = {
  title: "Inception",
  original_title: "Inception",
  year: 2010,
  tmdb_id: 27205,
  imdb_id: "1375666",
  resolution: "1080p",
  video_codec: "x264",
  audio_codec: "DTS",
  frame_rate: "23.976fps",
  color_space: "HDR",
  aspect_ratio: "16x9",
  audio_channels: "5.1",
  source: "BluRay",
  release_group: "MediaAI",
  season: 1,
  episode: 1,
  season_episode: "S01E01",
  rating: "8.8",
  subtitle_language: "CHS",
  custom_text: "Custom",
};

const EPISODE_NAMING_SAMPLE_DATA: Record<string, string | number> = {
  ...DEFAULT_NAMING_SAMPLE_DATA,
  title: "\u6743\u529b\u7684\u6e38\u620f",
  original_title: "Game of Thrones",
  year: 2011,
  tmdb_id: 1399,
  imdb_id: "0944947",
  season: 1,
  episode: 1,
  season_episode: "S01E01",
};

export type NamingBuilderMessages = {
  elements: Record<string, string>;
  validation: Record<string, string>;
  sample: Record<string, string>;
  warnings?: Record<string, string>;
};

function formatBuilderMessage(template: string, values: Record<string, string | number>) {
  return Object.entries(values).reduce(
    (message, [key, value]) => message.replace(new RegExp(`\\{${key}\\}`, "g"), String(value)),
    template,
  );
}

export function elementLabel(definition: NamingElementDefinition, messages: NamingBuilderMessages): string {
  return messages.elements[definition.labelKey] || definition.key;
}

function definitionByKey(key: string) {
  return NAMING_ELEMENT_DEFINITIONS.find((item) => item.key === key);
}

function definitionByVariable(variable: string) {
  const baseVariable = variable.split(":")[0];
  return NAMING_ELEMENT_DEFINITIONS.find((item) => item.variable === baseVariable);
}

function mergeElementFormat(
  definition: NamingElementDefinition | undefined,
  rawFormat: NamingElementFormat | undefined,
): NamingElementFormat | undefined {
  const merged = {
    ...(definition?.format || {}),
    ...(rawFormat || {}),
  };
  return Object.keys(merged).length ? merged : undefined;
}

export function createNamingElement(
  key: string,
  messages: NamingBuilderMessages,
  suffix = Date.now(),
): NamingTemplateElement {
  const definition = definitionByKey(key) ?? NAMING_ELEMENT_DEFINITIONS[0];
  return {
    id: `${definition.key}-${suffix}-${Math.random().toString(36).slice(2, 8)}`,
    key: definition.key,
    label: elementLabel(definition, messages),
    variable: definition.variable,
    format: definition.format ? { ...definition.format } : undefined,
    customText: definition.key === "custom" ? messages.sample.customText : undefined,
  };
}

export function elementsFromPreset(
  type: NamingTemplateType,
  preset: string,
  messages: NamingBuilderMessages,
): NamingTemplateElement[] {
  const keys = NAMING_TEMPLATE_PRESETS[type][preset] ?? NAMING_TEMPLATE_PRESETS[type].standard;
  return keys.map((key, index) => createNamingElement(key, messages, index));
}

export function serializeNamingElements(elements: NamingTemplateElement[]): string {
  return JSON.stringify(
    elements.map(({ key, label, variable, format, customText }) => ({
      key,
      label,
      variable,
      ...(format ? { format } : {}),
      ...(customText ? { customText } : {}),
    })),
  );
}

function normalizeElement(
  raw: Partial<NamingTemplateElement>,
  index: number,
  messages: NamingBuilderMessages,
): NamingTemplateElement {
  const definition = definitionByKey(String(raw.key || "")) ?? definitionByVariable(String(raw.variable || ""));
  const variable = String(raw.variable || definition?.variable || "title");
  return {
    id: `${raw.key || variable}-${index}-${Math.random().toString(36).slice(2, 8)}`,
    key: String(raw.key || definition?.key || variable.split(":")[0]),
    label: String(raw.label || (definition ? elementLabel(definition, messages) : variable)),
    variable,
    format: mergeElementFormat(definition, raw.format),
    customText: raw.customText,
  };
}

export function parseNamingTemplate(
  value: string,
  type: NamingTemplateType,
  messages: NamingBuilderMessages,
): NamingTemplateElement[] {
  const trimmed = value.trim();
  if (!trimmed) {
    return elementsFromPreset(type, "standard", messages);
  }

  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      const elements = parsed
        .filter((item): item is Partial<NamingTemplateElement> => typeof item === "object" && item !== null)
        .map((item, index) => normalizeElement(item, index, messages));
      return elements.length ? elements : elementsFromPreset(type, "standard", messages);
    }
  } catch {
    // Fall back to the legacy string parser.
  }

  const matches = [...trimmed.matchAll(/\{([^{}]+)\}/g)];
  const elements = matches.map((match, index) => normalizeElement({ variable: match[1] }, index, messages));
  return elements.length ? elements : elementsFromPreset(type, "standard", messages);
}

export function validateNamingElements(
  elements: NamingTemplateElement[],
  messages: NamingBuilderMessages,
): NamingValidationError[] {
  const errors: NamingValidationError[] = [];
  const seen = new Map<string, number>();
  let hasTitle = false;

  elements.forEach((element, index) => {
    const variable = element.variable.trim();
    const baseVariable = variable.split(":")[0];
    if (baseVariable === "title") {
      hasTitle = true;
    }
    if (!VARIABLE_PATTERN.test(variable)) {
      errors.push({ index, message: messages.validation.invalidVariable });
    }
    if (/[\\/:*?"<>|]/.test(element.customText || "")) {
      errors.push({ index, message: messages.validation.illegalCustomText });
    }
    if (seen.has(baseVariable)) {
      const message = formatBuilderMessage(messages.validation.duplicateElement, { variable: `{${baseVariable}}` });
      errors.push({ index, message });
      errors.push({ index: seen.get(baseVariable) ?? 0, message });
    } else {
      seen.set(baseVariable, index);
    }
  });

  if (!hasTitle) {
    errors.push({ index: -1, message: messages.validation.titleRequired });
  }
  return errors;
}

export function validateNamingSeparator(separator: string, messages: NamingBuilderMessages): string | null {
  if (separator.length > 5 || /[\\/:*?"<>|]/.test(separator)) {
    return messages.validation.invalidSeparator;
  }
  return null;
}

type SemanticConflict = {
  variables: [string, string];
  messageKey: string;
};

const SEMANTIC_CONFLICTS: SemanticConflict[] = [
  { variables: ["season", "season_episode"], messageKey: "seasonWithSeasonEpisode" },
  { variables: ["episode", "season_episode"], messageKey: "episodeWithSeasonEpisode" },
  { variables: ["title", "original_title"], messageKey: "titleWithOriginalTitle" },
];

export function detectNamingSemanticWarnings(
  elements: NamingTemplateElement[],
  messages: NamingBuilderMessages,
): string[] {
  const variables = new Set(elements.map((element) => element.variable.split(":")[0]));
  return SEMANTIC_CONFLICTS.filter(({ variables: [left, right] }) => variables.has(left) && variables.has(right))
    .map(({ messageKey }) => messages.warnings?.[messageKey])
    .filter((message): message is string => Boolean(message));
}

function wrapBracketStyle(value: string, bracketStyle: NamingElementFormat["bracketStyle"] | undefined) {
  if (bracketStyle === "round") {
    return `(${value})`;
  }
  if (bracketStyle === "curly") {
    return `{${value}}`;
  }
  if (bracketStyle === "square" || bracketStyle === undefined) {
    return `[${value}]`;
  }
  return value;
}

function formatSampleValue(element: NamingTemplateElement, sampleData: Record<string, string | number>) {
  if (element.key === "custom") {
    return element.customText || sampleData.custom_text || "Custom";
  }
  const raw = sampleData[element.variable.split(":")[0]] ?? sampleData[element.key] ?? element.label;
  if (typeof raw === "number" && element.format?.pad) {
    return String(raw).padStart(element.format.pad, "0");
  }
  if (element.key === "tmdb_id" || element.key === "imdb_id") {
    const prefixed = `${element.format?.prefix || ""}${raw}`;
    return wrapBracketStyle(prefixed, element.format?.bracketStyle);
  }
  if (element.format?.prefix) {
    return `${element.format.prefix}${raw}`;
  }
  return String(raw);
}

function buildSampleData(messages: NamingBuilderMessages, type: NamingTemplateType = "movie") {
  const baseSampleData = type === "episode" ? EPISODE_NAMING_SAMPLE_DATA : DEFAULT_NAMING_SAMPLE_DATA;
  return {
    ...baseSampleData,
    title: type === "episode" ? baseSampleData.title : messages.sample.title || baseSampleData.title,
    original_title: type === "episode" ? baseSampleData.original_title : messages.sample.originalTitle || baseSampleData.original_title,
    source: messages.sample.source || baseSampleData.source,
    release_group: messages.sample.releaseGroup || baseSampleData.release_group,
    custom_text: messages.sample.customText || baseSampleData.custom_text,
  };
}

export function buildNamingElementPreview(element: NamingTemplateElement, messages: NamingBuilderMessages): string {
  return String(formatSampleValue(element, buildSampleData(messages))).trim();
}

export function buildNamingPreview(
  elements: NamingTemplateElement[],
  separator: string,
  messages: NamingBuilderMessages,
  type: NamingTemplateType = "movie",
  extension = "mkv",
): string {
  if (!elements.length) {
    return messages.validation.emptyTemplate;
  }
  if (!elements.some((element) => element.variable.split(":")[0] === "title")) {
    return messages.validation.titleMissingPreview;
  }
  const sampleData = buildSampleData(messages, type);
  const safeSeparator = separator || ".";
  const body = elements
    .map((element) => String(formatSampleValue(element, sampleData)).trim())
    .filter(Boolean)
    .join(safeSeparator);
  return `${body}.${extension}`;
}

export function elementHasError(errors: NamingValidationError[], index: number): boolean {
  return errors.some((error) => error.index === index);
}

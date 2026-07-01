const SENSITIVE_WORD_SEPARATOR = "||";

function normalizeLegacySeparators(input: string): string {
  return input.replace(/\r\n|\r|\n/g, SENSITIVE_WORD_SEPARATOR);
}

function normalizeWords(words: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  words
    .map((word) => word.trim())
    .filter(Boolean)
    .forEach((word) => {
      const key = word.toLocaleLowerCase();
      if (!seen.has(key)) {
        normalized.push(word);
        seen.add(key);
      }
    });
  return normalized;
}

export function parseSensitiveWordsInput(input: string): string[] {
  return normalizeWords(normalizeLegacySeparators(input).split(SENSITIVE_WORD_SEPARATOR));
}

export function formatSensitiveWordsInput(words: unknown): string {
  if (typeof words === "string") {
    return parseSensitiveWordsInput(words).join(SENSITIVE_WORD_SEPARATOR);
  }
  if (!Array.isArray(words)) {
    return "";
  }
  return normalizeWords(words.filter((word): word is string => typeof word === "string")).join(SENSITIVE_WORD_SEPARATOR);
}

export const themeDefinitions = [
  { id: "default", labelKey: "defaultPalette" },
  { id: "candy", labelKey: "candyPalette" },
  { id: "contrast", labelKey: "contrastPalette" },
  { id: "ocean", labelKey: "oceanPalette" },
  { id: "forest", labelKey: "forestPalette" },
  { id: "amber", labelKey: "amberPalette" },
] as const;

export type ThemePalette = typeof themeDefinitions[number]["id"];

export function isThemePalette(value: unknown): value is ThemePalette {
  return themeDefinitions.some(theme => theme.id === value);
}

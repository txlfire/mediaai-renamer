export type AiProviderDefaultConfig = {
  model: string;
  baseUrl: string;
};

export function getAiProviderDefaults(provider: string): AiProviderDefaultConfig {
  if (provider === "openai_compatible") {
    return {
      model: "gpt-4o-mini",
      baseUrl: "https://api.openai.com/v1",
    };
  }
  if (provider === "custom") {
    return {
      model: "",
      baseUrl: "https://",
    };
  }
  return {
    model: "deepseek-chat",
    baseUrl: "https://api.deepseek.com",
  };
}

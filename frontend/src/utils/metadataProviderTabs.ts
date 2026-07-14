import type { MetadataProviderConfig } from "../api/client";

export const SUPPLEMENTAL_METADATA_PROVIDER_KEYS = [
  "imdb",
  "bangumi",
  "tvdb",
  "douban_proxy",
] as const;

export type SupplementalMetadataProviderKey = (typeof SUPPLEMENTAL_METADATA_PROVIDER_KEYS)[number];

export type SupplementalMetadataProviderTab = {
  key: SupplementalMetadataProviderKey;
  provider: MetadataProviderConfig | null;
};

export function resolveSupplementalMetadataProviderTabs(
  providers: MetadataProviderConfig[],
): SupplementalMetadataProviderTab[] {
  const providerMap = new Map(providers.map((provider) => [provider.provider, provider]));
  return SUPPLEMENTAL_METADATA_PROVIDER_KEYS.map((key) => ({
    key,
    provider: providerMap.get(key) ?? null,
  }));
}

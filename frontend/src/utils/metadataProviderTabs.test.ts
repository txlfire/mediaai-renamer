import { describe, expect, it } from "vitest";

import type { MetadataProviderConfig } from "../api/client";
import {
  SUPPLEMENTAL_METADATA_PROVIDER_KEYS,
  resolveSupplementalMetadataProviderTabs,
} from "./metadataProviderTabs";

describe("补充元数据源 Tab", () => {
  it("固定按 IMDb、Bangumi、TVDB、豆瓣代理顺序展示", () => {
    const providers: MetadataProviderConfig[] = [
      {
        id: 1,
        provider: "imdb",
        enabled: false,
        priority: 2,
        base_url: "https://www.imdb.com",
        has_api_key: false,
        timeout_seconds: 10,
        max_retries: 1,
        created_at: "2026-07-12T00:00:00Z",
        updated_at: "2026-07-12T00:00:00Z",
      },
      {
        id: 3,
        provider: "tvdb",
        enabled: false,
        priority: 20,
        base_url: "https://api4.thetvdb.com/v4",
        has_api_key: true,
        timeout_seconds: 10,
        max_retries: 1,
        created_at: "2026-07-12T00:00:00Z",
        updated_at: "2026-07-12T00:00:00Z",
      },
      {
        id: 2,
        provider: "bangumi",
        enabled: true,
        priority: 30,
        base_url: "https://api.bgm.tv",
        has_api_key: false,
        timeout_seconds: 10,
        max_retries: 1,
        created_at: "2026-07-12T00:00:00Z",
        updated_at: "2026-07-12T00:00:00Z",
      },
    ];

    const tabs = resolveSupplementalMetadataProviderTabs(providers);

    expect(SUPPLEMENTAL_METADATA_PROVIDER_KEYS).toEqual(["imdb", "bangumi", "tvdb", "douban_proxy"]);
    expect(tabs.map((tab) => tab.key)).toEqual(["imdb", "bangumi", "tvdb", "douban_proxy"]);
    expect(tabs.find((tab) => tab.key === "imdb")?.provider?.id).toBe(1);
    expect(tabs.find((tab) => tab.key === "bangumi")?.provider?.id).toBe(2);
    expect(tabs.find((tab) => tab.key === "douban_proxy")?.provider).toBeNull();
  });
});

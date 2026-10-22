import { describe, expect, it, vi } from "vitest";
import { SessionChangedError } from "../utils/sessionGeneration";
const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock("./client", () => ({ apiClient: mocks }));
import { fetchCloudDriveSettings, saveCloudDriveSettings, type CloudDriveSettingsValues } from "./cloudDriveSettings";
describe("CD2 系统设置接口", () => {
  it("使用独立本地设置接口", async () => {
    mocks.get.mockResolvedValueOnce({ data: { revision: 0 } }); await expect(fetchCloudDriveSettings()).resolves.toEqual({ revision: 0 });
    expect(mocks.get).toHaveBeenCalledWith("/settings/cloud-drive");
    const values = {} as CloudDriveSettingsValues; mocks.put.mockResolvedValueOnce({ data: { revision: 1 } });
    await expect(saveCloudDriveSettings(values, 0)).resolves.toEqual({ revision: 1 });
    expect(mocks.put).toHaveBeenCalledWith("/settings/cloud-drive", { values, expectedRevision: 0 });
  });
  it("只显示固定错误，不透出请求对象或远端原文", async () => {
    mocks.put.mockRejectedValueOnce({ response: { data: { detail: "configuration_changed" } }, private: "synthetic-secret" });
    await expect(saveCloudDriveSettings({} as CloudDriveSettingsValues, 0)).rejects.toThrow("重新加载");
    mocks.get.mockRejectedValueOnce({ response: { data: { detail: "synthetic-secret" } } });
    await expect(fetchCloudDriveSettings()).rejects.toThrow("配置读写失败");
    mocks.put.mockRejectedValueOnce(new Error("synthetic-lost-response"));
    await expect(saveCloudDriveSettings({} as CloudDriveSettingsValues, 0)).rejects.toThrow("未能确认保存结果");
  });
  it("保留会话失效语义", async () => {
    const error = new SessionChangedError(); mocks.get.mockRejectedValueOnce(error); await expect(fetchCloudDriveSettings()).rejects.toBe(error);
  });
});

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import App from "./App";
import { generateConfig } from "./api";

const apiMocks = vi.hoisted(() => ({
  getSavedConfigurationsMock: vi.fn(),
  getScraperStatusMock: vi.fn(),
  deleteSavedConfigurationMock: vi.fn(),
  getMarketPriceRangeMock: vi.fn(),
  getPartPriceRangesMock: vi.fn(),
  getStorageInventoryMock: vi.fn(),
}));

vi.mock("./api", () => ({
  generateConfig: vi.fn(),
  getSavedConfigurations: apiMocks.getSavedConfigurationsMock,
  getScraperStatus: apiMocks.getScraperStatusMock,
  deleteSavedConfiguration: apiMocks.deleteSavedConfigurationMock,
  getMarketPriceRange: apiMocks.getMarketPriceRangeMock,
  getPartPriceRanges: apiMocks.getPartPriceRangesMock,
  getStorageInventory: apiMocks.getStorageInventoryMock,
}));

const savedConfigurationsFixture = [
  {
    id: 1,
    name: "ゲーム優先",
    budget: 150000,
    usage: "gaming",
    usage_display: "Gaming",
    total_price: 140000,
    cpu_data: null,
    cpu_cooler_data: null,
    gpu_data: null,
    motherboard_data: null,
    memory_data: null,
    storage_data: null,
    storage2_data: null,
    storage3_data: null,
    os_data: null,
    psu_data: null,
    case_data: null,
    case_fan_data: null,
    created_at: "2026-03-14T10:00:00Z",
  },
  {
    id: 2,
    name: "事務用省電力",
    budget: 90000,
    usage: "general",
    usage_display: "General",
    total_price: 82000,
    cpu_data: null,
    cpu_cooler_data: null,
    gpu_data: null,
    motherboard_data: null,
    memory_data: null,
    storage_data: null,
    storage2_data: null,
    storage3_data: null,
    os_data: null,
    psu_data: null,
    case_data: null,
    case_fan_data: null,
    created_at: "2026-03-14T11:00:00Z",
  },
];

describe("App history panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getSavedConfigurationsMock.mockResolvedValue(savedConfigurationsFixture);
    apiMocks.getScraperStatusMock.mockResolvedValue({
      cache_enabled: true,
      cache_ttl_seconds: 3600,
      last_update_time: "2026-04-09T00:20:31.830142+00:00",
      cached_categories: ["cpu"],
      total_parts_in_db: 2,
      retry_count: 3,
      rate_limit_delay: 1,
    });
    apiMocks.getMarketPriceRangeMock.mockResolvedValue({
      min: 100000,
      max: 400000,
      default: 250000,
      currency: "JPY",
      sources: {},
    });
    apiMocks.getPartPriceRangesMock.mockResolvedValue({
      gpu: { label: "GPU", min: 30000, max: 80000, avg: 55000, count: 2 },
    });
    apiMocks.getStorageInventoryMock.mockResolvedValue({
      total_count: 2,
      latest_updated_at: "2026-03-15T10:00:00Z",
      interface_summary: [
        { interface: "nvme", label: "NVMe", count: 1, min_price: 10000, max_price: 10000, avg_price: 10000 },
        { interface: "sata", label: "SATA", count: 1, min_price: 8000, max_price: 8000, avg_price: 8000 },
      ],
      capacity_summary: [
        {
          capacity_gb: 1024,
          label: "1TB",
          count: 1,
          min_price: 10000,
          max_price: 10000,
          avg_price: 10000,
          items: [
            {
              id: 1,
              name: "Sample NVMe 1TB",
              price: 10000,
              url: "https://example.com/storage-1",
              capacity_gb: 1024,
              capacity_label: "1TB",
              interface: "nvme",
              interface_label: "NVMe",
              form_factor: "M.2",
              updated_at: "2026-03-15T10:00:00Z",
            },
          ],
        },
      ],
    });
    apiMocks.deleteSavedConfigurationMock.mockResolvedValue(undefined);
  });

  it("shows dedicated toast when OS required budget error occurs", async () => {
    vi.mocked(generateConfig).mockRejectedValueOnce(
      new Error("OS必須予算不足: CPUクーラー/ケースを調整しても不足しています。最低でも¥80,000が必要です。")
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "PC構成を提案してもらう" }));

    await waitFor(() => {
      expect(screen.getByText("OS必須予算不足")).toBeInTheDocument();
    });
    const highlightedToast = screen.getByText("OS必須予算不足").closest("div.fixed.top-20.right-4");
    expect(highlightedToast).toBeTruthy();
    expect(highlightedToast).toHaveTextContent("要点: OSを維持すると予算内に収まりません。");
    expect(highlightedToast).toHaveTextContent("推奨予算: ¥80,000");
  });

  it("filters history by usage", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });

    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    expect(screen.getByText("ゲーム優先")).toBeInTheDocument();
    expect(screen.getByText("事務用省電力")).toBeInTheDocument();

    const usageFilter = screen.getAllByRole("combobox")[0];
    fireEvent.change(usageFilter, { target: { value: "gaming" } });

    await waitFor(() => {
      expect(screen.getByText("ゲーム優先")).toBeInTheDocument();
      expect(screen.queryByText("事務用省電力")).not.toBeInTheDocument();
    });
  });

  it("shows latest scraper status in developer panel", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "▶ スクレイパー" }));

    expect(screen.getByText("最新スクレイプ状況")).toBeInTheDocument();
    expect(screen.getByText("DB件数")).toBeInTheDocument();
    expect(screen.getByText("2 件")).toBeInTheDocument();
    expect(screen.getByText("キャッシュ")).toBeInTheDocument();
    expect(screen.getByText("有効")).toBeInTheDocument();
    expect(screen.getByText("TTL")).toBeInTheDocument();
    expect(screen.getByText("3,600 秒")).toBeInTheDocument();
    const statusPanel = screen.getByText("最新スクレイプ状況").closest("div.fixed.bottom-16.left-4");
    expect(statusPanel).toHaveTextContent("最終更新");
    expect(statusPanel).toHaveTextContent("2026/4/9");
  });

  it("opens delete modal and calls delete API", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    const gamingCard = screen.getByText("ゲーム優先").closest("div.w-full.text-left.border");
    expect(gamingCard).toBeTruthy();
    await userEvent.click(within(gamingCard as HTMLElement).getByRole("button", { name: "削除" }));

    expect(screen.getByText("構成を削除しますか？")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "削除する" }));

    await waitFor(() => {
      expect(apiMocks.deleteSavedConfigurationMock).toHaveBeenCalledWith(1);
    });
  });

  it("bulk-deletes all history items and shows toast", async () => {
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    const selectors = screen.getAllByRole("combobox");
    fireEvent.change(selectors[1], { target: { value: "all" } });

    await userEvent.click(screen.getByRole("button", { name: "全件 2 件を削除" }));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
      expect(apiMocks.deleteSavedConfigurationMock).toHaveBeenCalledWith(1);
      expect(apiMocks.deleteSavedConfigurationMock).toHaveBeenCalledWith(2);
      expect(screen.getByText("2 件を削除しました")).toBeInTheDocument();
    });
  });

  it("filters history by keyword query", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    const queryInput = screen.getByPlaceholderText("ID・パーツ名・金額で検索");

    await userEvent.clear(queryInput);
    await userEvent.type(queryInput, "82000");

    await waitFor(() => {
      expect(screen.queryByText("ゲーム優先")).not.toBeInTheDocument();
      expect(screen.getByText("事務用省電力")).toBeInTheDocument();
    });

    await userEvent.clear(queryInput);
    await userEvent.type(queryInput, "id 1");

    await waitFor(() => {
      expect(screen.getByText("ゲーム優先")).toBeInTheDocument();
      expect(screen.queryByText("事務用省電力")).not.toBeInTheDocument();
    });

    await userEvent.clear(queryInput);
    await userEvent.type(queryInput, "general");

    await waitFor(() => {
      expect(screen.queryByText("ゲーム優先")).not.toBeInTheDocument();
      expect(screen.getByText("事務用省電力")).toBeInTheDocument();
    });
  });

  it("shows saved configuration name as primary label", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    expect(screen.getByText("ゲーム優先")).toBeInTheDocument();
    expect(screen.getByText("事務用省電力")).toBeInTheDocument();
  });

  it("matches history search query against saved configuration name", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    const queryInput = screen.getByPlaceholderText("ID・パーツ名・金額で検索");

    await userEvent.clear(queryInput);
    await userEvent.type(queryInput, "事務用省電力");

    await waitFor(() => {
      expect(screen.getByText("事務用省電力")).toBeInTheDocument();
      expect(screen.queryByText("ゲーム優先")).not.toBeInTheDocument();
    });
  });

  it("applies usage filter and keyword query together", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    const selectors = screen.getAllByRole("combobox");
    const usageFilter = selectors[0];
    const queryInput = screen.getByPlaceholderText("ID・パーツ名・金額で検索");

    fireEvent.change(usageFilter, { target: { value: "general" } });
    await userEvent.clear(queryInput);
    await userEvent.type(queryInput, "140000");

    await waitFor(() => {
      expect(screen.queryByText("ゲーム優先")).not.toBeInTheDocument();
      expect(screen.queryByText("事務用省電力")).not.toBeInTheDocument();
      expect(screen.getByText("条件に一致する保存済み構成はありません。")).toBeInTheDocument();
    });

    await userEvent.clear(queryInput);
    await userEvent.type(queryInput, "82000");

    await waitFor(() => {
      expect(screen.queryByText("ゲーム優先")).not.toBeInTheDocument();
      expect(screen.getByText("事務用省電力")).toBeInTheDocument();
    });
  });

  it("closes history panel when returning to form from result view", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "保存履歴 2" });
    await userEvent.click(screen.getByRole("button", { name: "保存履歴 2" }));

    const gamingCard = screen.getByText("ゲーム優先").closest("div.w-full.text-left.border");
    expect(gamingCard).toBeTruthy();
    await userEvent.click(within(gamingCard as HTMLElement).getByRole("button", { name: "詳細を開く" }));

    await screen.findByRole("button", { name: "別の構成を生成" });
    await userEvent.click(screen.getByRole("button", { name: "別の構成を生成" }));

    await waitFor(() => {
      expect(screen.queryByText("保存済み構成")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "保存履歴 2" })).toBeInTheDocument();
    });
  });

  it("sets middle preset budget when switching usage to general", async () => {
    render(<App />);

    await screen.findByRole("radio", { name: /汎用・家庭用/ });
    const generalUsageRadio = screen.getByRole("radio", { name: /汎用・家庭用/ });
    await userEvent.click(generalUsageRadio);

    await waitFor(() => {
      expect(screen.getByRole("spinbutton")).toHaveValue(224980);
    });
  });
});

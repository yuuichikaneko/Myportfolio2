import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { GenerateConfigResponse, SavedConfigurationResponse } from "./api";
import { ResultView } from "./ResultView";

async function renderResultView(config: GenerateConfigResponse | SavedConfigurationResponse) {
  render(<ResultView config={config} onBack={() => {}} />);
  await screen.findByText("構成提案が完成しました！");
}

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    getLatestGpuPerformance: vi.fn(async () => ({
      snapshot: {
        id: 1,
        source_name: "Dospara GPU Performance",
        source_url: "https://example.com/gpu",
        updated_at_source: "2026-04-04",
        score_note: "Higher is better",
        parser_version: "v1",
        fetched_at: "2026-04-04T00:00:00Z",
      },
      entries: {
        count: 3,
        next: null,
        previous: null,
        results: [
          {
            gpu_name: "RTX 5070 12GB",
            model_key: "RTX 5070",
            vendor: "nvidia",
            vram_gb: 12,
            perf_score: 3931,
            detail_url: "https://example.com/5070",
            rank_global: 12,
          },
          {
            gpu_name: "RX 9070 XT 16GB",
            model_key: "RX 9070 XT",
            vendor: "amd",
            vram_gb: 16,
            perf_score: 3673,
            detail_url: "https://example.com/9070xt",
            rank_global: 13,
          },
          {
            gpu_name: "RTX 5060 Ti 16GB",
            model_key: "RTX 5060 TI",
            vendor: "nvidia",
            vram_gb: 16,
            perf_score: 2500,
            detail_url: "https://example.com/5060ti",
            rank_global: 14,
          },
        ],
      },
    })),
    compareGpuPerformance: vi.fn(async () => ({
      snapshot_id: 1,
      requested_models: ["RTX 5070", "RX 9070 XT"],
      missing_models: [],
      results: [
        {
          gpu_name: "RTX 5070 12GB",
          model_key: "RTX 5070",
          vendor: "nvidia",
          vram_gb: 12,
          perf_score: 3931,
          detail_url: "https://example.com/5070",
          rank_global: 12,
        },
        {
          gpu_name: "RX 9070 XT 16GB",
          model_key: "RX 9070 XT",
          vendor: "amd",
          vram_gb: 16,
          perf_score: 3673,
          detail_url: "https://example.com/9070xt",
          rank_global: 13,
        },
      ],
    })),
    getLatestCpuSelectionMaterial: vi.fn(async () => ({
      source_name: "dospara_cpu_comparison_pages",
      source_urls: ["https://example.com/amd", "https://example.com/intel"],
      exclude_intel_13_14: true,
      entry_count: 4,
      excluded_count: 3,
      entries: {
        count: 4,
        next: null,
        previous: null,
        results: [
          { vendor: "intel", model_name: "Core i5-12400F", perf_score: 3918, source_url: "https://example.com/intel" },
          { vendor: "amd", model_name: "Ryzen 9 9950X3D", perf_score: 7390, price: 114470, value_score: 0.064558, source_url: "https://example.com/amd3" },
          { vendor: "amd", model_name: "Ryzen 7 9700X", perf_score: 3904, price: 40180, value_score: 0.097163, source_url: "https://example.com/amd4" },
          { vendor: "amd", model_name: "Ryzen 7 7800X3D", perf_score: 3609, price: 49800, value_score: 0.07247, source_url: "https://example.com/amd" },
          { vendor: "amd", model_name: "Ryzen 5 9600X", perf_score: 3163, price: 35280, value_score: 0.089654, source_url: "https://example.com/amd5" },
        ],
      },
    })),
    compareCpuSelectionMaterial: vi.fn(async () => ({
      requested_models: ["Core i5-12400F", "Ryzen 7 9700X", "Ryzen 7 7800X3D", "Ryzen 5 9600X"],
      missing_models: [],
      results: [
        { vendor: "intel", model_name: "Core i5-12400F", perf_score: 3918, source_url: "https://example.com/intel" },
        { vendor: "amd", model_name: "Ryzen 7 9700X", perf_score: 3904, price: 40180, value_score: 0.097163, source_url: "https://example.com/amd4" },
        { vendor: "amd", model_name: "Ryzen 7 7800X3D", perf_score: 3609, price: 49800, value_score: 0.07247, source_url: "https://example.com/amd" },
        { vendor: "amd", model_name: "Ryzen 5 9600X", perf_score: 3163, price: 35280, value_score: 0.089654, source_url: "https://example.com/amd5" },
      ],
    })),
    getSavedConfigurationById: vi.fn(async () => ({
      id: 999,
      budget: 220000,
      usage: "gaming",
      usage_display: "ゲーミングPC",
      total_price: 180000,
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
      created_at: "2026-04-05T12:00:00Z",
    })),
    createSavedConfiguration: vi.fn(async () => ({
      id: 1202,
      budget: 220000,
      usage: "gaming",
      usage_display: "ゲーミングPC",
      total_price: 189000,
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
      created_at: "2026-04-05T12:00:00Z",
    })),
    getPartsByType: vi.fn(async (partType: string) => {
      if (partType === "cpu") {
        return [
          {
            id: 101,
            part_type: "cpu",
            part_type_display: "CPU",
            name: "AMD Ryzen 7 9700X BOX",
            price: 49800,
            specs: { socket: "AM5", memory_type: "DDR5" },
            url: "https://example.com/cpu9700x",
            scraped_at: "2026-04-05T12:00:00Z",
            updated_at: "2026-04-05T12:00:00Z",
          },
        ];
      }
      return [];
    }),
  };
});

describe("ResultView", () => {
  it("renders gaming cpu ranking entries and highlights current cpu", async () => {
    const config: GenerateConfigResponse = {
      usage: "gaming",
      budget: 200000,
      configuration_id: 1,
      total_price: 188000,
      estimated_power_w: 550,
      parts: [
        { category: "cpu", name: "Ryzen 7 7800X3D BOX", price: 60000, url: "https://example.com/cpu" },
        { category: "gpu", name: "RTX 5070", price: 90000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    await waitFor(() => {
      expect(screen.getByText("ゲーミングCPU順位（AMD・スペック順）")).toBeInTheDocument();
    });

    expect(screen.getByText("新規生成ID: 1")).toBeInTheDocument();

    expect(screen.getByText("スペック重視ではX3Dを優先して性能順に並べています。")).toBeInTheDocument();
    expect(screen.getByText("元データ: 4 / 除外: 3")).toBeInTheDocument();
    expect(screen.getByText("Ryzen 7 7800X3D")).toBeInTheDocument();
    expect(screen.getByText("Ryzen 5 9600X")).toBeInTheDocument();
    expect(screen.queryByText("Ryzen 9 9950X3D")).not.toBeInTheDocument();
    expect(screen.queryByText("INTEL")).not.toBeInTheDocument();

    const badges = screen.getAllByText("現在の構成");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("renders gaming cpu cost ranking entries when build priority is cost", async () => {
    const config: GenerateConfigResponse = {
      usage: "gaming",
      build_priority: "cost",
      budget: 200000,
      configuration_id: 2,
      total_price: 178000,
      estimated_power_w: 520,
      parts: [
        { category: "cpu", name: "Ryzen 7 7800X3D BOX", price: 60000, url: "https://example.com/cpu" },
        { category: "gpu", name: "RTX 5070", price: 90000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    await waitFor(() => {
      expect(screen.getByText("ゲーミングCPU選択テーブル（AMD・コスパ重視）")).toBeInTheDocument();
    });

    expect(screen.getByText("新規生成ID: 2")).toBeInTheDocument();

    expect(screen.getByText("コスパ重視では性能/価格で選択候補を並べています。")).toBeInTheDocument();
    expect(screen.getByText("Ryzen 7 9700X")).toBeInTheDocument();
    expect(screen.getByText("Ryzen 5 9600X")).toBeInTheDocument();
    expect(screen.getByText("0.097163")).toBeInTheDocument();
    expect(screen.getByText("0.089654")).toBeInTheDocument();
    expect(screen.queryByText("Ryzen 9 9950X3D")).not.toBeInTheDocument();
  });

  it("highlights only the exact current cpu model in the cpu ranking table", async () => {
    const config: GenerateConfigResponse = {
      usage: "gaming",
      budget: 180000,
      configuration_id: 3,
      total_price: 168000,
      estimated_power_w: 500,
      parts: [
        { category: "cpu", name: "AMD Ryzen 7 7800X BOX", price: 15000, url: "https://example.com/cpu-7800x" },
        { category: "gpu", name: "RTX 4060", price: 70000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    await waitFor(() => {
      expect(screen.getByText("Ryzen 7 7800X3D")).toBeInTheDocument();
    });

    expect(screen.getByText("Ryzen 7 7800X3D")).toBeInTheDocument();
    expect(screen.queryAllByText("現在の構成")).toHaveLength(0);
  });

  it("shows saved configuration id label for saved results", async () => {
    const savedConfig: SavedConfigurationResponse = {
      id: 787,
      budget: 169980,
      usage: "gaming",
      usage_display: "ゲーミングPC",
      total_price: 169868,
      cpu_data: {
        id: 1,
        part_type: "cpu",
        part_type_display: "CPU",
        name: "AMD Ryzen 7 7700 BOX",
        price: 41800,
        specs: {},
        url: "https://example.com/cpu",
        scraped_at: "2026-04-05T12:00:00Z",
        updated_at: "2026-04-05T12:00:00Z",
      },
      cpu_cooler_data: null,
      gpu_data: {
        id: 2,
        part_type: "gpu",
        part_type_display: "グラフィックボード",
        name: "RTX 3050 6GB",
        price: 32360,
        specs: {},
        url: "https://example.com/gpu",
        scraped_at: "2026-04-05T12:00:00Z",
        updated_at: "2026-04-05T12:00:00Z",
      },
      motherboard_data: null,
      memory_data: null,
      storage_data: null,
      storage2_data: null,
      storage3_data: null,
      os_data: null,
      psu_data: null,
      case_data: null,
      created_at: "2026-04-05T12:00:00Z",
    };

    await renderResultView(savedConfig);

    expect(screen.getByText("保存済み構成ID: 787")).toBeInTheDocument();
    expect(screen.queryByText("新規生成ID: 787")).not.toBeInTheDocument();
  });

  it("shows market budget correction note when market adjustment is applied", async () => {
    const config: GenerateConfigResponse = {
      usage: "gaming",
      build_priority: "spec",
      budget: 520000,
      requested_budget: 574980,
      budget_auto_adjusted: true,
      market_budget_adjusted: true,
      market_budget_note: "予算を補正しました。相場データ（high帯）に基づき、予算を¥520,000へ補正しました。",
      configuration_id: 3,
      total_price: 498000,
      estimated_power_w: 560,
      parts: [
        { category: "cpu", name: "Ryzen 7 9800X3D BOX", price: 62180, url: "https://example.com/cpu" },
        { category: "gpu", name: "RTX 5070", price: 90000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("相場補正")).toBeInTheDocument();
    expect(screen.queryByText("構成を自動調整しました")).not.toBeInTheDocument();
    expect(screen.getByText("予算を補正しました。相場データ（high帯）に基づき、予算を¥520,000へ補正しました。")).toBeInTheDocument();
    expect(screen.getByText("引き下げ")).toBeInTheDocument();
    expect(screen.getByText("予算: ￥574,980 → ￥520,000")).toBeInTheDocument();
  });

  it("shows raise-direction label when budget correction increases budget", async () => {
    const config: GenerateConfigResponse = {
      usage: "gaming",
      build_priority: "cost",
      budget: 180000,
      requested_budget: 120000,
      budget_auto_adjusted: true,
      market_budget_adjusted: true,
      market_budget_note: "予算を補正しました。相場データ（low帯）に基づき、予算を¥180,000へ引き上げました。",
      configuration_id: 11,
      total_price: 168000,
      estimated_power_w: 320,
      parts: [
        { category: "cpu", name: "Ryzen 5 7600", price: 32000, url: "https://example.com/cpu" },
        { category: "gpu", name: "RTX 4060", price: 45000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("引き上げ")).toBeInTheDocument();
    expect(screen.getByText("予算: ￥120,000 → ￥180,000")).toBeInTheDocument();
  });

  it("shows configuration auto-adjustment separately from budget correction", async () => {
    const config: GenerateConfigResponse = {
      usage: "gaming",
      build_priority: "spec",
      budget: 186978,
      requested_budget: 186978,
      budget_auto_adjusted: true,
      market_budget_adjusted: false,
      part_adjustments: [
        {
          category: "cpu",
          category_label: "CPU",
          from_name: "AMD Ryzen 7 5700X3D BOX",
          from_price: 35800,
          to_name: "AMD Ryzen 7 7800X3D BOX",
          to_price: 48000,
          reason: "用途・予算帯・方針に合わせてCPUを再選定しました。",
        },
      ],
      configuration_id: 10,
      total_price: 174016,
      estimated_power_w: 244,
      parts: [
        { category: "cpu", name: "AMD Ryzen 7 7800X3D BOX", price: 48000, url: "https://example.com/cpu" },
        { category: "gpu", name: "Palit NE63050018JE-1072F (GeForce RTX 3050 StormX 6GB)", price: 29800, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("構成を自動調整しました")).toBeInTheDocument();
    expect(screen.queryByText("予算を補正しました")).not.toBeInTheDocument();
    expect(screen.getByText("構成パーツと選定条件を自動調整しました。")).toBeInTheDocument();
    expect(screen.getByText("構成変更の内訳")).toBeInTheDocument();
    expect(screen.getByText("CPU: AMD Ryzen 7 5700X3D BOX → AMD Ryzen 7 7800X3D BOX")).toBeInTheDocument();
    expect(screen.getByText("理由: 用途・予算帯・方針に合わせてCPUを再選定しました。")).toBeInTheDocument();
  });

  it("shows creator budget tier and build priority labels", async () => {
    const config: GenerateConfigResponse = {
      usage: "creator",
      build_priority: "cost",
      budget: 684980,
      budget_tier: "premium",
      budget_tier_label: "プレミアム",
      requested_budget: 684980,
      configuration_id: 1025,
      total_price: 669627,
      estimated_power_w: 546,
      parts: [
        { category: "cpu", name: "Ryzen 9 9950X", price: 120000, url: "https://example.com/cpu" },
        { category: "gpu", name: "RTX 5070", price: 100000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("予算帯: プレミアム")).toBeInTheDocument();
    expect(screen.getByText("構成方針: コスト重視")).toBeInTheDocument();
  });

  it("shows a creator cpu recommendation note for game streaming", async () => {
    const config: GenerateConfigResponse = {
      usage: "creator",
      build_priority: "spec",
      budget: 1314478,
      requested_budget: 1314478,
      configuration_id: 1064,
      total_price: 996079,
      estimated_power_w: 366,
      parts: [
        { category: "cpu", name: "AMD Ryzen 9 9950X", price: 120000, url: "https://example.com/cpu" },
        { category: "gpu", name: "NVIDIA RTX PRO 4500 Blackwell BOX (RTX PRO 4500 32GB)", price: 259800, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("ゲーム配信をするならRyzen 9 9950X3Dがおすすめです。")).toBeInTheDocument();
  });

  it("prefers backend budget tier labels over local inference", async () => {
    const config: GenerateConfigResponse = {
      usage: "creator",
      build_priority: "cost",
      budget: 684980,
      budget_tier: "premium",
      budget_tier_label: "プレミアム(backend)",
      requested_budget: 684980,
      configuration_id: 1026,
      total_price: 669627,
      estimated_power_w: 546,
      parts: [
        { category: "cpu", name: "Ryzen 9 9950X", price: 120000, url: "https://example.com/cpu" },
        { category: "gpu", name: "RTX 5070", price: 100000, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("予算帯: プレミアム(backend)")).toBeInTheDocument();
    expect(screen.queryByText("予算帯: プレミアム")).not.toBeInTheDocument();
  });

  it("shows a creator gpu explanation in the gpu section", async () => {
    const config: GenerateConfigResponse = {
      usage: "creator",
      build_priority: "spec",
      budget: 478478,
      requested_budget: 478478,
      configuration_id: 1030,
      total_price: 464380,
      estimated_power_w: 366,
      parts: [
        { category: "cpu", name: "Intel Core Ultra 7 265F BOX", price: 52380, url: "https://example.com/cpu" },
        { category: "gpu", name: "ASRock Radeon AI PRO R9700 Creator 32GB", price: 259800, url: "https://example.com/gpu" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("クリエイターPCではVRAM容量を優先し、同条件ならNVIDIAを優先します。NVIDIA対応アプリが多く、高解像度編集や重い3D素材向けの選定です。")).toBeInTheDocument();
  });

  it("shows bundled cpu cooler note when dedicated cooler is not selected", async () => {
    const config: GenerateConfigResponse = {
      usage: "general",
      build_priority: "cost",
      budget: 54980,
      requested_budget: 54980,
      configuration_id: 1091,
      total_price: 52448,
      estimated_power_w: 160,
      parts: [
        { category: "cpu", name: "AMD Ryzen 5 3400G BOX", price: 10500, url: "https://example.com/cpu" },
        { category: "gpu", name: "内蔵GPU（統合グラフィックス）", price: 0, url: "https://example.com/cpu" },
        { category: "motherboard", name: "ASRock A520M-HDV", price: 5680, url: "https://example.com/mb" },
        { category: "memory", name: "CFD DDR4 8GB", price: 11660, url: "https://example.com/memory" },
        { category: "os", name: "Windows 11 HOME", price: 16480, url: "https://example.com/os" },
        { category: "psu", name: "400W PSU", price: 4580, url: "https://example.com/psu" },
        { category: "case", name: "ZALMAN T3 PLUS", price: 3548, url: "https://example.com/case" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("付属CPUクーラーを使用")).toBeInTheDocument();
    expect(screen.getByText("CPUクーラーは未選択ですが、CPU付属クーラーを前提にしています。")).toBeInTheDocument();
  });

  it("shows bundled cpu cooler note for ai builds as well", async () => {
    const config: GenerateConfigResponse = {
      usage: "ai",
      build_priority: "spec",
      budget: 533478,
      requested_budget: 533478,
      configuration_id: 1141,
      total_price: 523748,
      estimated_power_w: 526,
      parts: [
        { category: "cpu", name: "Intel Core Ultra 7 265K BOX", price: 47780, url: "https://example.com/cpu" },
        { category: "gpu", name: "GeForce RTX 5080 16GB", price: 309800, url: "https://example.com/gpu" },
        { category: "motherboard", name: "ASUS TUF GAMING B860M-PLUS WIFI", price: 25980, url: "https://example.com/mb" },
        { category: "memory", name: "DDR5 48GB", price: 88000, url: "https://example.com/memory" },
        { category: "storage", name: "NVMe 1TB", price: 19800, url: "https://example.com/storage" },
        { category: "os", name: "Windows 11 HOME", price: 16480, url: "https://example.com/os" },
        { category: "psu", name: "1000W PSU", price: 16580, url: "https://example.com/psu" },
        { category: "case", name: "ZALMAN T3 PLUS", price: 3548, url: "https://example.com/case" },
      ],
    };

    await renderResultView(config);

    expect(screen.getByText("付属CPUクーラーを使用")).toBeInTheDocument();
    expect(screen.getByText("CPUクーラーは未選択ですが、CPU付属クーラーを前提にしています。")).toBeInTheDocument();
  });

  it("allows manual part replacement from result screen", async () => {
    const user = userEvent.setup();
    const config: GenerateConfigResponse = {
      usage: "gaming",
      build_priority: "cost",
      budget: 220000,
      requested_budget: 220000,
      configuration_id: 1201,
      total_price: 180000,
      estimated_power_w: 430,
      parts: [
        { category: "cpu", name: "AMD Ryzen 5 7600 BOX", price: 32000, url: "https://example.com/cpu7600" },
        { category: "motherboard", name: "B650M BOARD", price: 18000, url: "https://example.com/b650", specs: { socket: "AM5", memory_type: "DDR4" } },
        { category: "memory", name: "DDR4 32GB", price: 12000, url: "https://example.com/ddr4", specs: { memory_type: "DDR4" } },
        { category: "gpu", name: "RTX 4060", price: 45000, url: "https://example.com/gpu4060" },
      ],
    };

    await renderResultView(config);

    await user.click(screen.getByRole("button", { name: "CPUを変更" }));

    await waitFor(() => {
      expect(screen.getByText("表示件数: 1 / 1（非互換はグレー表示）")).toBeInTheDocument();
      expect(screen.getByText("AMD Ryzen 7 9700X BOX")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.queryByText("候補を読み込み中です…")).not.toBeInTheDocument();
    });

    expect(screen.getByText("警告: CPUの対応メモリ規格が現在のマザーボードと一致しません。 CPUの対応メモリ規格が現在のメモリと一致しません。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /AMD Ryzen 7 9700X BOX/ }));

    expect(screen.getByText("非互換候補を選択しますか？")).toBeInTheDocument();
    expect(screen.getByText("この候補を選択")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "この候補を選択" }));

    await waitFor(() => {
      expect(screen.getByText("手動で構成を変更中です。")).toBeInTheDocument();
    });

    expect(screen.getByText("AMD Ryzen 7 9700X BOX")).toBeInTheDocument();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  compareCpuSelectionMaterial,
  deleteSavedConfiguration,
  compareGpuPerformance,
  generateConfig,
  getPartsByType,
  getSavedConfigurations,
  getLatestCpuSelectionMaterial,
  getScraperStatus,
  getStorageInventory,
  getLatestGpuPerformance,
} from "./api";

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("gets scraper status", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          cache_enabled: true,
          cache_ttl_seconds: 3600,
          last_update_time: null,
          cached_categories: ["cpu"],
          total_parts_in_db: 1,
          retry_count: 3,
          rate_limit_delay: 1,
        }),
        { status: 200 }
      )
    );

    const result = await getScraperStatus();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/scraper-status/summary/",
      undefined
    );
    expect(result.total_parts_in_db).toBe(1);
  });

  it("gets saved configurations from paginated response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          count: 1,
          next: null,
          previous: null,
          results: [
            {
              id: 10,
              budget: 150000,
              usage: "gaming",
              usage_display: "Gaming",
              total_price: 140000,
              cpu_data: null,
              gpu_data: null,
              motherboard_data: null,
              memory_data: null,
              storage_data: null,
              os_data: null,
              psu_data: null,
              case_data: null,
              created_at: "2026-03-14T10:00:00Z",
            },
          ],
        }),
        { status: 200 }
      )
    );

    const result = await getSavedConfigurations();

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(10);
  });

  it("sends delete request for saved configuration", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));

    await deleteSavedConfiguration(7);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/configurations/7/",
      { method: "DELETE" }
    );
  });

  it("returns generated config response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          usage: "gaming",
          budget: 150000,
          minimum_gaming_gpu_perf_score: 5000,
          selected_gpu_perf_score: 5297,
          configuration_id: 1,
          total_price: 140000,
          estimated_power_w: 550,
          parts: [{ category: "cpu", name: "Sample", price: 30000, url: "https://example.com" }],
        }),
        { status: 200 }
      )
    );

    const result = await generateConfig({ budget: 150000, usage: "gaming" });

    expect(result.configuration_id).toBe(1);
    expect(result.minimum_gaming_gpu_perf_score).toBe(5000);
    expect(result.selected_gpu_perf_score).toBe(5297);
    expect(result.parts[0].category).toBe("cpu");
  });

  it("gets storage inventory summary", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
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
        }),
        { status: 200 }
      )
    );

    const result = await getStorageInventory();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/storage-inventory/",
      undefined
    );
    expect(result.total_count).toBe(2);
    expect(result.capacity_summary[0].items[0].interface_label).toBe("NVMe");
  });

  it("gets parts by type with storage category filter", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );

    await getPartsByType("storage", { slotCategory: "storage2", storageCategory: "sata" });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/parts/by_type/?type=storage&slot=storage2&storage_category=sata",
      undefined
    );
  });

  it("gets latest gpu performance snapshot", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          snapshot: {
            id: 3,
            source_name: "Dospara GPU Performance",
            source_url: "https://example.com/gpu",
            updated_at_source: "2026-03-15T10:00:00Z",
            score_note: "Higher is better",
            parser_version: "v1",
            fetched_at: "2026-03-15T10:00:00Z",
          },
          entries: {
            count: 2,
            next: null,
            previous: null,
            results: [
              {
                gpu_name: "RTX 5070",
                model_key: "RTX 5070",
                vendor: "nvidia",
                vram_gb: 12,
                perf_score: 3931,
                detail_url: "https://example.com/5070",
                rank_global: 12,
              },
            ],
          },
        }),
        { status: 200 }
      )
    );

    const result = await getLatestGpuPerformance();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/gpu-performance/latest/",
      undefined
    );
    expect(result.snapshot.id).toBe(3);
    expect(result.entries.results[0].model_key).toBe("RTX 5070");
  });

  it("compares gpu performance for multiple models", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          snapshot_id: 3,
          requested_models: ["RTX 5070", "RX 9070 XT"],
          missing_models: [],
          results: [
            {
              gpu_name: "RTX 5070",
              model_key: "RTX 5070",
              vendor: "nvidia",
              vram_gb: 12,
              perf_score: 3931,
              detail_url: "https://example.com/5070",
              rank_global: 12,
            },
            {
              gpu_name: "RX 9070 XT",
              model_key: "RX 9070 XT",
              vendor: "amd",
              vram_gb: 16,
              perf_score: 3673,
              detail_url: "https://example.com/9070xt",
              rank_global: 18,
            },
          ],
        }),
        { status: 200 }
      )
    );

    const result = await compareGpuPerformance(["RTX 5070", "RX 9070 XT"]);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/gpu-performance/compare/",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ models: ["RTX 5070", "RX 9070 XT"] }),
      }
    );
    expect(result.results).toHaveLength(2);
    expect(result.results[1].model_key).toBe("RX 9070 XT");
  });

  it("gets latest cpu selection material", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          source_name: "dospara_cpu_comparison_pages",
          source_urls: ["https://example.com/amd", "https://example.com/intel"],
          exclude_intel_13_14: true,
          entry_count: 2,
          excluded_count: 1,
          entries: {
            count: 2,
            next: null,
            previous: null,
            results: [
              { vendor: "amd", model_name: "Ryzen 7 7800X3D", perf_score: 3609, source_url: "https://example.com/amd" },
            ],
          },
        }),
        { status: 200 }
      )
    );

    const result = await getLatestCpuSelectionMaterial();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/cpu-selection-material/latest/",
      undefined
    );
    expect(result.exclude_intel_13_14).toBe(true);
    expect(result.entries.results[0].model_name).toContain("7800X3D");
  });

  it("compares cpu selection material", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          requested_models: ["Ryzen 7 7800X3D", "Core i5-12400F"],
          missing_models: [],
          results: [
            { vendor: "amd", model_name: "Ryzen 7 7800X3D", perf_score: 3609, source_url: "https://example.com/amd" },
            { vendor: "intel", model_name: "Core i5-12400F", perf_score: 3918, source_url: "https://example.com/intel" },
          ],
        }),
        { status: 200 }
      )
    );

    const result = await compareCpuSelectionMaterial(["Ryzen 7 7800X3D", "Core i5-12400F"]);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/cpu-selection-material/compare/",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ models: ["Ryzen 7 7800X3D", "Core i5-12400F"] }),
      }
    );
    expect(result.results).toHaveLength(2);
  });
});

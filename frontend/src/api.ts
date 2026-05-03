const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8001/api";

/**
 * 構成生成APIで受け付ける用途コード。
 * 正規値は gaming / general / creator / business / workstation。
 * 旧データ互換のため、ai / standard / video_editing も受け付ける。
 */
export type UsageCode =
  | "gaming"
  | "general"
  | "creator"
  | "business"
  | "workstation"
  | "ai"
  | "standard"
  | "video_editing";

export interface CustomBudgetWeights {
  cpu: number;
  cpu_cooler: number;
  gpu: number;
  motherboard: number;
  memory: number;
  storage: number;
  os: number;
  psu: number;
  case: number;
}

// API との通信失敗を共通化する。
async function safeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch {
    throw new Error(
      `API server is unreachable: ${API_BASE_URL}. Start Django API server (python django/manage.py runserver 8001).`
    );
  }
}

// API エラーの本文を整形し、必要なら推奨予算を付加する。
async function parseApiError(response: Response, fallbackMessage: string): Promise<Error> {
  try {
    const error = await response.json();
    const detail = error.detail || fallbackMessage;
    const recommended = error.recommended_budget_min_for_x3d;
    if (typeof recommended === "number" && Number.isFinite(recommended)) {
      return new Error(`${detail} 推奨予算: ¥${recommended.toLocaleString("ja-JP")}`);
    }
    return new Error(detail);
  } catch {
    return new Error(fallbackMessage);
  }
}

/**
 * 構成生成APIのリクエスト形式。
 * 予算と用途コードを必須とし、追加の詳細条件は任意で指定する。
 */
export interface GenerateConfigRequest {
  budget: number;
  usage: UsageCode;
  selected_budget_tier?: "low" | "middle" | "high" | "premium";
  name?: string;
  cooler_type?: "air" | "liquid";
  radiator_size?: "120" | "240" | "360";
  cooling_profile?: "silent" | "performance";
  case_size?: "mini" | "mid" | "full";
  case_fan_policy?: "auto" | "silent" | "airflow";
  cpu_vendor?: "intel" | "amd";
  build_priority?: "cost" | "spec";
  storage_preference?: "ssd" | "hdd";
  min_storage_capacity_gb?: number;
  storage2_part_id?: number;
  storage3_part_id?: number;
  os_edition?: "auto" | "home" | "pro";
  custom_budget_weights?: CustomBudgetWeights;
  cpu_part_id?: number;
}

export interface PartResponse {
  category: string;
  name: string;
  price: number;
  url: string;
  specs?: Record<string, unknown> | null;
}

export interface PartAdjustmentResponse {
  category: string;
  category_label?: string;
  from_name: string;
  from_price: number;
  to_name: string;
  to_price: number;
  reason: string;
}

/**
 * 構成生成APIのレスポンス形式。
 * 選択した用途コード、合計金額、構成パーツ一覧を返す。
 */
export interface GenerateConfigResponse {
  name?: string;
  usage: UsageCode;
  budget: number;
  budget_tier?: "low" | "middle" | "high" | "premium";
  budget_tier_label?: string;
  requested_budget?: number;
  budget_auto_adjusted?: boolean;
  market_budget_adjusted?: boolean;
  market_budget_note?: string | null;
  recommended_budget_min_for_x3d?: number | null;
  x3d_enforced?: boolean;
  minimum_gaming_gpu_perf_score?: number;
  selected_gpu_perf_score?: number;
  selected_gpu_gaming_tier_label?: string;
  message?: string;
  part_adjustments?: PartAdjustmentResponse[];
  cooler_type?: "air" | "liquid" | "any";
  radiator_size?: "120" | "240" | "360" | "any";
  cooling_profile?: "silent" | "performance" | "balanced";
  case_size?: "mini" | "mid" | "full" | "any";
  case_fan_policy?: "auto" | "silent" | "airflow";
  cpu_vendor?: "intel" | "amd" | "any";
  build_priority?: "cost" | "spec" | "balanced";
  storage_preference?: "ssd" | "hdd";
  os_edition?: "auto" | "home" | "pro";
  custom_budget_weights?: Record<string, number> | null;
  configuration_id: number | null;
  total_price: number;
  estimated_power_w: number;
  parts: PartResponse[];
}

export interface SavedPartResponse {
  id: number;
  part_type: string;
  part_type_display: string;
  name: string;
  price: number;
  specs: Record<string, unknown>;
  url: string;
  scraped_at: string;
  updated_at: string;
}

export interface SavedConfigurationResponse {
  id: number;
  name?: string;
  budget: number;
  budget_tier?: "low" | "middle" | "high" | "premium";
  budget_tier_label?: string;
  usage: UsageCode;
  usage_display: string;
  total_price: number;
  cpu_data: SavedPartResponse | null;
  cpu_cooler_data: SavedPartResponse | null;
  gpu_data: SavedPartResponse | null;
  motherboard_data: SavedPartResponse | null;
  memory_data: SavedPartResponse | null;
  storage_data: SavedPartResponse | null;
  storage2_data: SavedPartResponse | null;
  storage3_data: SavedPartResponse | null;
  os_data: SavedPartResponse | null;
  psu_data: SavedPartResponse | null;
  case_data: SavedPartResponse | null;
  case_fan_data: SavedPartResponse | null;
  created_at: string;
}

export interface CreateSavedConfigurationRequest {
  name?: string;
  budget: number;
  usage: "gaming" | "general" | "creator" | "business" | "workstation" | "video_editing" | "ai" | "standard";
  cpu: number | null;
  cpu_cooler: number | null;
  gpu: number | null;
  motherboard: number | null;
  memory: number | null;
  storage: number | null;
  storage2: number | null;
  storage3: number | null;
  os: number | null;
  psu: number | null;
  case: number | null;
  case_fan: number | null;
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export async function generateConfig(
  request: GenerateConfigRequest
): Promise<GenerateConfigResponse> {
  const requestPromise = safeFetch(`${API_BASE_URL}/configurations/generate/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    cache: "no-store",
    body: JSON.stringify(request),
  });

  let timeoutHandle: number | null = null;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timeoutHandle = window.setTimeout(() => {
      reject(new Error("構成生成がタイムアウトしました。条件を緩めるか、もう一度お試しください。"));
    }, 60000);
  });

  const response = await Promise.race([requestPromise, timeoutPromise]);
  if (timeoutHandle !== null) {
    window.clearTimeout(timeoutHandle);
  }

  if (!response.ok) {
    throw await parseApiError(response, "Failed to generate configuration");
  }

  return response.json();
}

export interface CategoryStat {
  part_type: string;
  label: string;
  count: number;
  min_price: number | null;
  max_price: number | null;
}

export interface ScraperStatus {
  cache_enabled: boolean;
  cache_ttl_seconds: number;
  last_update_time: string | null;
  cached_categories: string[];
  category_stats: CategoryStat[];
  total_parts_in_db: number;
  retry_count: number;
  rate_limit_delay: number;
}

export interface MarketPriceRangeSource {
  url: string;
  min: number | null;
  max: number | null;
  count: number;
}

export interface MarketPriceRangeResponse {
  min: number;
  max: number;
  default: number;
  currency: string;
  gaming_x3d_cpu_floor?: number;
  gaming_x3d_recommended_min?: number;
  sources: Record<string, MarketPriceRangeSource>;
}

export async function getScraperStatus(): Promise<ScraperStatus> {
  const response = await safeFetch(`${API_BASE_URL}/scraper-status/summary/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get scraper status");
  }

  return response.json();
}

export async function getMarketPriceRange(): Promise<MarketPriceRangeResponse> {
  const response = await safeFetch(`${API_BASE_URL}/market-price-range/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get market price range");
  }

  return response.json();
}

export async function getSavedConfigurations(): Promise<SavedConfigurationResponse[]> {
  const response = await safeFetch(`${API_BASE_URL}/configurations/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get saved configurations");
  }

  const data: PaginatedResponse<SavedConfigurationResponse> = await response.json();
  return data.results;
}

export async function getSavedConfigurationById(id: number): Promise<SavedConfigurationResponse> {
  const response = await safeFetch(`${API_BASE_URL}/configurations/${id}/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get saved configuration");
  }

  return response.json();
}

export async function createSavedConfiguration(
  request: CreateSavedConfigurationRequest
): Promise<SavedConfigurationResponse> {
  const response = await safeFetch(`${API_BASE_URL}/configurations/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw await parseApiError(response, "Failed to create saved configuration");
  }

  return response.json();
}

export async function updateSavedConfiguration(
  id: number,
  request: Partial<CreateSavedConfigurationRequest>
): Promise<SavedConfigurationResponse> {
  const response = await safeFetch(`${API_BASE_URL}/configurations/${id}/`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw await parseApiError(response, "Failed to update saved configuration");
  }

  return response.json();
}

export async function deleteSavedConfiguration(id: number): Promise<void> {
  const response = await safeFetch(`${API_BASE_URL}/configurations/${id}/`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw await parseApiError(response, "Failed to delete saved configuration");
  }
}

interface GetPartsByTypeOptions {
  slotCategory?: string;
  storageCategory?: "nvme" | "sata";
}

export async function getPartsByType(
  partType: string,
  options: GetPartsByTypeOptions = {},
): Promise<SavedPartResponse[]> {
  const searchParams = new URLSearchParams({ type: partType });
  if (options.slotCategory) {
    searchParams.set("slot", options.slotCategory);
  }
  if (options.storageCategory) {
    searchParams.set("storage_category", options.storageCategory);
  }
  const response = await safeFetch(`${API_BASE_URL}/parts/by_type/?${searchParams.toString()}`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get parts by type");
  }

  return response.json();
}

export interface PartPriceRange {
  label: string;
  min: number | null;
  max: number | null;
  avg: number | null;
  count: number;
}

export type PartPriceRangesResponse = Record<string, PartPriceRange>;

export interface StorageInventoryItem {
  id: number;
  name: string;
  price: number;
  url: string;
  capacity_gb: number;
  capacity_label: string;
  interface: "nvme" | "sata" | "other";
  interface_label: string;
  form_factor: string | null;
  updated_at: string;
}

export interface StorageCapacitySummary {
  capacity_gb: number;
  label: string;
  count: number;
  min_price: number | null;
  max_price: number | null;
  avg_price: number | null;
  items: StorageInventoryItem[];
}

export interface StorageInterfaceSummary {
  interface: "nvme" | "sata" | "other";
  label: string;
  count: number;
  min_price: number | null;
  max_price: number | null;
  avg_price: number | null;
}

export interface StorageInventoryResponse {
  total_count: number;
  latest_updated_at: string | null;
  capacity_summary: StorageCapacitySummary[];
  interface_summary: StorageInterfaceSummary[];
}

export interface GpuPerformanceSnapshotMeta {
  id: number;
  source_name: string;
  source_url: string;
  updated_at_source: string | null;
  score_note: string;
  parser_version: string;
  fetched_at: string;
}

export interface GpuPerformanceEntryResponse {
  gpu_name: string;
  model_key: string;
  vendor: string;
  vram_gb: number | null;
  perf_score: number;
  detail_url: string;
  rank_global: number;
}

export interface GpuPerformanceLatestResponse {
  snapshot: GpuPerformanceSnapshotMeta;
  entries: PaginatedResponse<GpuPerformanceEntryResponse>;
}

export interface GpuPerformanceCompareResponse {
  snapshot_id: number;
  requested_models: string[];
  missing_models: string[];
  results: GpuPerformanceEntryResponse[];
}

export interface CpuSelectionEntryResponse {
  vendor: string;
  model_name: string;
  perf_score: number;
  price?: number | null;
  value_score?: number | null;
  cost_rank?: number | null;
  source_url: string;
}

export interface CpuSelectionMaterialLatestResponse {
  source_name: string;
  source_urls: string[];
  exclude_intel_13_14: boolean;
  entry_count: number;
  excluded_count: number;
  entries: PaginatedResponse<CpuSelectionEntryResponse>;
}

export interface CpuSelectionMaterialCompareResponse {
  requested_models: string[];
  missing_models: string[];
  results: CpuSelectionEntryResponse[];
}

export async function getPartPriceRanges(): Promise<PartPriceRangesResponse> {
  const response = await safeFetch(`${API_BASE_URL}/part-price-ranges/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get part price ranges");
  }

  return response.json();
}

export async function getStorageInventory(): Promise<StorageInventoryResponse> {
  const response = await safeFetch(`${API_BASE_URL}/storage-inventory/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get storage inventory");
  }

  return response.json();
}

export async function getLatestGpuPerformance(): Promise<GpuPerformanceLatestResponse> {
  const response = await safeFetch(`${API_BASE_URL}/gpu-performance/latest/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get GPU performance latest snapshot");
  }

  return response.json();
}

export async function compareGpuPerformance(models: string[]): Promise<GpuPerformanceCompareResponse> {
  const response = await safeFetch(`${API_BASE_URL}/gpu-performance/compare/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ models }),
  });

  if (!response.ok) {
    throw await parseApiError(response, "Failed to compare GPU performance");
  }

  return response.json();
}

export async function getLatestCpuSelectionMaterial(): Promise<CpuSelectionMaterialLatestResponse> {
  const response = await safeFetch(`${API_BASE_URL}/cpu-selection-material/latest/`);

  if (!response.ok) {
    throw await parseApiError(response, "Failed to get CPU selection material");
  }

  return response.json();
}

export async function compareCpuSelectionMaterial(models: string[]): Promise<CpuSelectionMaterialCompareResponse> {
  const response = await safeFetch(`${API_BASE_URL}/cpu-selection-material/compare/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ models }),
  });

  if (!response.ok) {
    throw await parseApiError(response, "Failed to compare CPU selection material");
  }

  return response.json();
}

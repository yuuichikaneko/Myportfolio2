import { useEffect, useMemo, useState } from "react";
import { ConfigForm } from "./ConfigForm";
import { ResultView } from "./ResultView";
import {
  CustomBudgetWeights,
  deleteSavedConfiguration,
  generateConfig,
  GenerateConfigResponse,
  getSavedConfigurationById,
  getSavedConfigurations,
  getScraperStatus,
  SavedConfigurationResponse,
  ScraperStatus,
  type UsageCode,
} from "./api";
import { normalizeUsageCode } from "./usageUtils";

interface OsBudgetToast {
  point: string;
  recommendedBudgetText: string;
}

const USAGE_LABELS_JA: Record<UsageCode, string> = {
  gaming: "ゲーム",
  general: "汎用",
  creator: "クリエイト",
  business: "ビジネス",
  workstation: "ワークステーション",
  ai: "ワークステーション",
  standard: "汎用",
  video_editing: "クリエイト",
};

function App() {
  const [result, setResult] = useState<GenerateConfigResponse | null>(null);
  const [selectedSavedConfig, setSelectedSavedConfig] = useState<SavedConfigurationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scraperStatus, setScraperStatus] = useState<ScraperStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [showStatus, setShowStatus] = useState(false);
  const [savedConfigurations, setSavedConfigurations] = useState<SavedConfigurationResponse[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyActionLoadingId, setHistoryActionLoadingId] = useState<number | null>(null);
  const [historyBulkDeleting, setHistoryBulkDeleting] = useState(false);
  const [deleteTargetConfig, setDeleteTargetConfig] = useState<SavedConfigurationResponse | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [historyUsageFilter, setHistoryUsageFilter] = useState<"all" | UsageCode>("all");
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyDeleteScope, setHistoryDeleteScope] = useState<"filtered" | "all">("filtered");
  const [historyToastMessage, setHistoryToastMessage] = useState<string | null>(null);
  const [osBudgetErrorToast, setOsBudgetErrorToast] = useState<OsBudgetToast | null>(null);

  useEffect(() => {
    if (!historyToastMessage) {
      return;
    }

    const timer = window.setTimeout(() => {
      setHistoryToastMessage(null);
    }, 2400);

    return () => window.clearTimeout(timer);
  }, [historyToastMessage]);

  useEffect(() => {
    if (!osBudgetErrorToast) {
      return;
    }

    const timer = window.setTimeout(() => {
      setOsBudgetErrorToast(null);
    }, 4500);

    return () => window.clearTimeout(timer);
  }, [osBudgetErrorToast]);

  const fetchSavedConfigurations = async () => {
    try {
      setHistoryError(null);
      const configurations = await getSavedConfigurations();
      setSavedConfigurations(configurations);
    } catch (err) {
      setHistoryError(
        err instanceof Error ? err.message : "保存済み構成の取得に失敗しました"
      );
    } finally {
      setHistoryLoading(false);
    }
  };

  // スクレイパー状態を定期的に取得
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const status = await getScraperStatus();
        setScraperStatus(status);
      } catch (err) {
        console.error("Failed to fetch scraper status:", err);
      } finally {
        setStatusLoading(false);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 30000); // 30秒ごとに更新

    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    fetchSavedConfigurations();
  }, []);

  useEffect(() => {
    if (!showHistory) {
      return;
    }
    setHistoryLoading(true);
    fetchSavedConfigurations();
  }, [showHistory]);

  useEffect(() => {
    if (!selectedSavedConfig) {
      return;
    }
    const stillExists = savedConfigurations.some((config) => config.id === selectedSavedConfig.id);
    if (!stillExists) {
      setSelectedSavedConfig(null);
    }
  }, [savedConfigurations, selectedSavedConfig]);

  const getConfigContentSignature = (config: SavedConfigurationResponse) => {
    const partIds = [
      config.cpu_data?.id ?? 0,
      config.cpu_cooler_data?.id ?? 0,
      config.gpu_data?.id ?? 0,
      config.motherboard_data?.id ?? 0,
      config.memory_data?.id ?? 0,
      config.storage_data?.id ?? 0,
      config.storage2_data?.id ?? 0,
      config.storage3_data?.id ?? 0,
      config.os_data?.id ?? 0,
      config.psu_data?.id ?? 0,
      config.case_data?.id ?? 0,
    ];
    return `${config.usage}|${config.budget}|${partIds.join("-")}`;
  };

  const uniqueSavedConfigurations = useMemo(() => {
    const bySignature = new Map<string, SavedConfigurationResponse>();
    for (const config of savedConfigurations) {
      const signature = getConfigContentSignature(config);
      const current = bySignature.get(signature);
      if (!current || new Date(config.created_at).getTime() > new Date(current.created_at).getTime()) {
        bySignature.set(signature, config);
      }
    }
    return Array.from(bySignature.values()).sort(
      (left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
    );
  }, [savedConfigurations]);

  const handleGenerateConfig = async (
    budget: number,
    usage: UsageCode,
    options: {
      name: string;
      coolerType: "air" | "liquid";
      radiatorSize: "120" | "240" | "360";
      coolingProfile: "silent" | "performance";
      caseSize: "mini" | "mid" | "full";
      caseFanPolicy: "auto" | "silent" | "airflow";
      cpuVendor: "any" | "intel" | "amd";
      buildPriority: "cost" | "spec";
      selectedBudgetTier?: "low" | "middle" | "high" | "premium";
      cpuPartId: number | null;
      storagePreference: "ssd" | "hdd";
      mainStorageCapacity: "512" | "1024" | "2048" | "4096";
      storage2PartId: number | null;
      storage3PartId: number | null;
      osEdition: "auto" | "home" | "pro";
      useCustomBudgetWeights: boolean;
      customBudgetWeights: CustomBudgetWeights;
    }
  ) => {
    setIsLoading(true);
    setError(null);
    setOsBudgetErrorToast(null);
    setResult(null);
    setSelectedSavedConfig(null);

    try {
      const response = await generateConfig({
        budget,
        usage,
        selected_budget_tier: options.selectedBudgetTier,
        name: options.name || undefined,
        cooler_type: options.coolerType,
        radiator_size: options.radiatorSize,
        cooling_profile: options.coolingProfile,
        case_size: options.caseSize,
        case_fan_policy: options.caseFanPolicy,
        cpu_vendor: options.cpuVendor === "any" ? undefined : options.cpuVendor,
        build_priority: options.buildPriority,
        storage_preference: options.storagePreference,
        min_storage_capacity_gb: Number(options.mainStorageCapacity),
        storage2_part_id: options.storage2PartId ?? undefined,
        storage3_part_id: options.storage3PartId ?? undefined,
        os_edition: options.osEdition,
        custom_budget_weights: options.useCustomBudgetWeights ? options.customBudgetWeights : undefined,
        cpu_part_id: options.cpuPartId ?? undefined,
      });
      setResult(response);
      setSelectedSavedConfig(null);
      setHistoryLoading(true);
      await fetchSavedConfigurations();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "予期しないエラーが発生しました";
      setError(errorMessage);
      if (errorMessage.includes("OS必須予算不足")) {
        const recommendedMatch = errorMessage.match(/¥\s*([\d,]+)/);
        const recommendedBudgetText = recommendedMatch
          ? `¥${recommendedMatch[1]}`
          : "予算を増やして再試行";
        setOsBudgetErrorToast({
          point: "OSを維持すると予算内に収まりません。",
          recommendedBudgetText,
        });
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleBack = () => {
    setResult(null);
    setSelectedSavedConfig(null);
    setError(null);
    setShowHistory(false);
  };

  const handleSavedFromResultView = async (saved: SavedConfigurationResponse) => {
    setSelectedSavedConfig(saved);
    setResult(null);
    setShowHistory(false);
    setHistoryLoading(true);
    await fetchSavedConfigurations();
    setHistoryToastMessage(`編集後の構成を保存しました (ID ${saved.id})`);
  };

  const handleSelectSavedConfig = (config: SavedConfigurationResponse) => {
    setSelectedSavedConfig(config);
    setResult(null);
    setError(null);
    setShowHistory(false);
  };

  const executeDeleteSavedConfig = async (config: SavedConfigurationResponse) => {
    setHistoryActionLoadingId(config.id);
    try {
      const signature = getConfigContentSignature(config);
      const duplicateConfigs = savedConfigurations.filter((item) => getConfigContentSignature(item) === signature);
      for (const item of duplicateConfigs) {
        await deleteSavedConfiguration(item.id);
      }

      if (selectedSavedConfig && duplicateConfigs.some((item) => item.id === selectedSavedConfig.id)) {
        setSelectedSavedConfig(null);
      }
      setHistoryLoading(true);
      await fetchSavedConfigurations();
      setHistoryToastMessage(
        duplicateConfigs.length > 1
          ? `同一内容 ${duplicateConfigs.length} 件を削除しました`
          : `ID ${config.id} を削除しました`
      );
    } catch (err) {
      setHistoryError(
        err instanceof Error ? err.message : "保存済み構成の削除に失敗しました"
      );
      setHistoryToastMessage("削除に失敗しました");
    } finally {
      setHistoryActionLoadingId(null);
    }
  };

  const handleDeleteSavedConfig = (config: SavedConfigurationResponse) => {
    setDeleteTargetConfig(config);
  };

  const confirmDeleteSavedConfig = async () => {
    if (!deleteTargetConfig) {
      return;
    }

    await executeDeleteSavedConfig(deleteTargetConfig);
    setDeleteTargetConfig(null);
  };

  const handleBulkDeleteVisibleHistory = async () => {
    const deleteCandidates = historyDeleteScope === "all" ? savedConfigurations : savedConfigurations.filter((config) => {
      const visibleSignatures = new Set(filteredHistory.map((item) => getConfigContentSignature(item)));
      return visibleSignatures.has(getConfigContentSignature(config));
    });
    if (deleteCandidates.length === 0) {
      return;
    }

    const scopeLabel = historyDeleteScope === "all" ? "全件" : "表示中";
    const confirmed = window.confirm(`${scopeLabel}の ${deleteCandidates.length} 件を削除しますか？`);
    if (!confirmed) {
      return;
    }

    setHistoryBulkDeleting(true);
    setHistoryError(null);
    try {
      for (const config of deleteCandidates) {
        await deleteSavedConfiguration(config.id);
      }

      if (selectedSavedConfig && deleteCandidates.some((config) => config.id === selectedSavedConfig.id)) {
        setSelectedSavedConfig(null);
      }

      setHistoryLoading(true);
      await fetchSavedConfigurations();
      setHistoryToastMessage(`${deleteCandidates.length} 件を削除しました`);
    } catch (err) {
      setHistoryError(
        err instanceof Error ? err.message : "保存済み構成の一括削除に失敗しました"
      );
      setHistoryToastMessage("一括削除に失敗しました");
    } finally {
      setHistoryBulkDeleting(false);
    }
  };

  const filteredHistory = uniqueSavedConfigurations.filter((config) => {
    if (historyUsageFilter !== "all" && normalizeUsageCode(config.usage) !== historyUsageFilter) {
      return false;
    }

    const query = historyQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }

    const partNames = [
      config.cpu_data?.name,
      config.gpu_data?.name,
      config.motherboard_data?.name,
      config.memory_data?.name,
      config.storage_data?.name,
      config.storage2_data?.name,
      config.storage3_data?.name,
      config.os_data?.name,
      config.psu_data?.name,
      config.case_data?.name,
    ]
      .filter((name): name is string => Boolean(name))
      .join(" ")
      .toLowerCase();

    const usageCode = normalizeUsageCode(config.usage, "general");
    const usageLabel = usageCode === "all" ? config.usage_display : USAGE_LABELS_JA[usageCode];

    const target = [
      `id ${config.id}`,
      config.name ?? "",
      usageCode,
      usageLabel,
      config.usage_display,
      config.total_price.toString(),
      config.budget.toString(),
      partNames,
    ]
      .join(" ")
      .toLowerCase();

    return target.includes(query);
  });

  const activeResult = result ?? selectedSavedConfig;
  const isDeveloperViewEnabled =
    import.meta.env.DEV ||
    (typeof window !== "undefined" && window.localStorage.getItem("myportfolio:developer-mode") === "1");
  const scraperCategoryStats = scraperStatus?.category_stats ?? [];
  const scraperCachedCategories = scraperStatus?.cached_categories ?? [];

  useEffect(() => {
    // 画面切り替え時（フォーム→結果 / 結果→フォーム）は常に先頭へ移動する。
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    });
  }, [activeResult]);

  return (
    <>
      <button
        onClick={() => setShowHistory(!showHistory)}
        className="fixed top-4 right-4 bg-slate-900 hover:bg-slate-800 text-white rounded px-4 py-2 text-sm font-medium transition-colors z-50"
        title={showHistory ? "保存済み構成を閉じる" : "保存済み構成を開く"}
      >
        {showHistory ? "✕ 保存履歴" : `保存履歴 ${uniqueSavedConfigurations.length}`}
      </button>

      {/* 開発者モードのみスクレイパーUIを表示 */}
      {isDeveloperViewEnabled && (
        <button
          onClick={() => setShowStatus(!showStatus)}
          className="fixed bottom-4 left-4 bg-blue-500 hover:bg-blue-600 text-white rounded px-3 py-2 text-sm font-medium transition-colors z-50"
          title={showStatus ? "スクレイパー情報を非表示" : "スクレイパー情報を表示"}
        >
          {showStatus ? "▼ スクレイパー" : "▶ スクレイパー"}
        </button>
      )}

      {/* スクレイパー統計情報パネル */}
      {isDeveloperViewEnabled && scraperStatus && !statusLoading && showStatus && (
        <div className="fixed bottom-16 left-4 bg-slate-50 border border-slate-300 rounded-lg p-4 shadow-lg text-sm max-w-xs z-50">
          <div className="font-semibold text-slate-700 mb-2">
            最新スクレイプ状況
          </div>
          <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
            <div className="rounded-md bg-white border border-slate-200 px-2 py-1.5">
              <div className="text-slate-500">DB件数</div>
              <div className="mt-0.5 font-semibold text-slate-800 tabular-nums">
                {scraperStatus.total_parts_in_db.toLocaleString("ja-JP")} 件
              </div>
            </div>
            <div className="rounded-md bg-white border border-slate-200 px-2 py-1.5">
              <div className="text-slate-500">キャッシュ</div>
              <div className="mt-0.5 font-semibold text-slate-800">
                {scraperStatus.cache_enabled ? "有効" : "無効"}
              </div>
            </div>
            <div className="rounded-md bg-white border border-slate-200 px-2 py-1.5">
              <div className="text-slate-500">TTL</div>
              <div className="mt-0.5 font-semibold text-slate-800 tabular-nums">
                {scraperStatus.cache_ttl_seconds.toLocaleString("ja-JP")} 秒
              </div>
            </div>
            <div className="rounded-md bg-white border border-slate-200 px-2 py-1.5">
              <div className="text-slate-500">再取得間隔</div>
              <div className="mt-0.5 font-semibold text-slate-800 tabular-nums">
                {scraperStatus.rate_limit_delay.toFixed(1)} 秒
              </div>
            </div>
          </div>
          <div className="mb-3 rounded-md bg-slate-100 px-2 py-1.5 text-xs text-slate-600">
            対象カテゴリ: {scraperCachedCategories.join("、") || "なし"}
          </div>
          <div className="mb-3 rounded-md bg-slate-100 px-2 py-1.5 text-xs text-slate-600">
            リトライ回数: {scraperStatus.retry_count}
          </div>
          <table className="w-full text-xs text-slate-600 border-collapse">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                <th className="text-left py-1 pr-2 font-medium">カテゴリ</th>
                <th className="text-right py-1 pr-2 font-medium">件数</th>
                <th className="text-right py-1 font-medium">価格帯 (¥)</th>
              </tr>
            </thead>
            <tbody>
              {scraperCategoryStats.map((stat) => (
                <tr key={stat.part_type} className="border-b border-slate-100 last:border-0">
                  <td className="py-1 pr-2">{stat.label}</td>
                  <td className="text-right py-1 pr-2 tabular-nums">{stat.count}</td>
                  <td className="text-right py-1 tabular-nums text-slate-500">
                    {stat.min_price != null && stat.max_price != null
                      ? `${stat.min_price.toLocaleString()}〜${stat.max_price.toLocaleString()}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-xs text-slate-400">
            最終更新: {scraperStatus.last_update_time ? new Date(scraperStatus.last_update_time).toLocaleString("ja-JP") : "未取得"}
          </div>
        </div>
      )}

      {showHistory && (
        <div className="fixed top-16 right-4 w-[22rem] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-5rem)] overflow-y-auto bg-white border border-slate-200 rounded-2xl shadow-2xl p-4 z-40">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-lg font-bold text-slate-900">保存済み構成</div>
              <div className="text-xs text-slate-500">内容一致は1件に集約表示 ・ {filteredHistory.length} 件表示中</div>
            </div>
            <button
              onClick={() => {
                setHistoryLoading(true);
                fetchSavedConfigurations();
              }}
              className="text-sm bg-slate-100 hover:bg-slate-200 text-slate-700 rounded px-3 py-1 transition-colors"
              disabled={historyBulkDeleting}
            >
              更新
            </button>
          </div>

          <div className="space-y-2 mb-4">
            <select
              value={historyUsageFilter}
              onChange={(e) => setHistoryUsageFilter(e.target.value as "all" | UsageCode)}
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-700"
            >
              <option value="all">用途: すべて</option>
              <option value="gaming">用途: ゲーミング</option>
              <option value="creator">用途: クリエイターPC</option>
              <option value="ai">用途: AI PC（ローカルAI）</option>
              <option value="general">用途: 汎用PC（事務・学習向け）</option>
            </select>
            <input
              value={historyQuery}
              onChange={(e) => setHistoryQuery(e.target.value)}
              placeholder="ID・パーツ名・金額で検索"
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-700"
            />
            <select
              value={historyDeleteScope}
              onChange={(e) => setHistoryDeleteScope(e.target.value as "filtered" | "all")}
              className="w-full border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700 bg-red-50"
            >
              <option value="filtered">一括削除対象: 表示中のみ</option>
              <option value="all">一括削除対象: 全件</option>
            </select>
            <button
              onClick={handleBulkDeleteVisibleHistory}
              disabled={historyBulkDeleting || (historyDeleteScope === "all" ? savedConfigurations.length === 0 : filteredHistory.length === 0)}
              className="w-full bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 text-sm rounded-lg px-3 py-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {historyBulkDeleting
                ? "一括削除中..."
                : historyDeleteScope === "all"
                  ? `全件 ${savedConfigurations.length} 件を削除`
                  : `表示中 ${filteredHistory.length} 件を削除`}
            </button>
          </div>

          {historyLoading ? (
            <div className="text-sm text-slate-500">読み込み中...</div>
          ) : historyError ? (
            <div className="text-sm text-red-500">{historyError}</div>
          ) : uniqueSavedConfigurations.length === 0 ? (
            <div className="text-sm text-slate-500">まだ保存済み構成はありません。</div>
          ) : filteredHistory.length === 0 ? (
            <div className="text-sm text-slate-500">条件に一致する保存済み構成はありません。</div>
          ) : (
            <div className="space-y-3">
              {filteredHistory.map((config) => (
                (() => {
                  const usageCode = normalizeUsageCode(config.usage, "general");
                  const usageLabel = usageCode === "all" ? config.usage_display : USAGE_LABELS_JA[usageCode];
                  return (
                <div
                  key={config.id}
                  className="w-full text-left border border-slate-200 hover:border-indigo-400 hover:bg-indigo-50 rounded-xl p-4 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div>
                      <div className="font-semibold text-slate-900">{config.name?.trim() ? config.name : usageLabel}</div>
                      <div className="text-xs text-slate-500">
                        {usageLabel} ・ ID {config.id} ・ {new Date(config.created_at).toLocaleString("ja-JP")}
                      </div>
                    </div>
                    <div className="text-sm font-bold text-indigo-600">
                      ¥{config.total_price.toLocaleString("ja-JP")}
                    </div>
                  </div>
                  <div className="text-sm text-slate-600">
                    予算 ¥{config.budget.toLocaleString("ja-JP")}
                  </div>
                  {config.os_data?.name && (
                    <div className="mt-1 text-xs text-slate-500">
                      OS: {config.os_data.name}
                    </div>
                  )}
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={async () => {
                        try {
                          const latest = await getSavedConfigurationById(config.id);
                          handleSelectSavedConfig(latest);
                        } catch {
                          handleSelectSavedConfig(config);
                        }
                      }}
                      className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white text-sm rounded-lg px-3 py-2 transition-colors"
                    >
                      詳細を開く
                    </button>
                    <button
                      onClick={() => handleDeleteSavedConfig(config)}
                      disabled={historyActionLoadingId === config.id || historyBulkDeleting}
                      className="bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 text-sm rounded-lg px-3 py-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {historyActionLoadingId === config.id ? "削除中..." : "削除"}
                    </button>
                  </div>
                </div>
                  );
                })()
              ))}
            </div>
          )}
        </div>
      )}

      {deleteTargetConfig && (
        <div className="fixed inset-0 bg-slate-900/45 flex items-center justify-center p-4 z-[70]">
          <div className="w-full max-w-md bg-white rounded-2xl shadow-2xl p-6">
            <h3 className="text-lg font-bold text-slate-900 mb-2">構成を削除しますか？</h3>
            <p className="text-sm text-slate-600 mb-4">この操作は取り消せません。</p>

            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-sm text-slate-700 space-y-1 mb-5">
              <div>ID: {deleteTargetConfig.id}</div>
              <div>
                用途: {
                  (() => {
                    const usageCode = normalizeUsageCode(deleteTargetConfig.usage, "general");
                    return usageCode === "all"
                      ? deleteTargetConfig.usage_display
                      : USAGE_LABELS_JA[usageCode];
                  })()
                }
              </div>
              <div>予算: ¥{deleteTargetConfig.budget.toLocaleString("ja-JP")}</div>
              <div>構成金額: ¥{deleteTargetConfig.total_price.toLocaleString("ja-JP")}</div>
              {deleteTargetConfig.name?.trim() && <div>保存名: {deleteTargetConfig.name}</div>}
            </div>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setDeleteTargetConfig(null)}
                disabled={historyActionLoadingId === deleteTargetConfig.id}
                className="bg-slate-100 hover:bg-slate-200 text-slate-700 px-4 py-2 rounded-lg text-sm disabled:opacity-50"
              >
                キャンセル
              </button>
              <button
                onClick={confirmDeleteSavedConfig}
                disabled={historyActionLoadingId === deleteTargetConfig.id}
                className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50"
              >
                {historyActionLoadingId === deleteTargetConfig.id ? "削除中..." : "削除する"}
              </button>
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="fixed top-0 left-0 right-0 bg-red-500 text-white p-4 text-center">
          エラー: {error}
        </div>
      )}

      {historyToastMessage && (
        <div className="fixed bottom-4 right-4 bg-slate-900 text-white text-sm px-4 py-3 rounded-lg shadow-lg z-[80]">
          {historyToastMessage}
        </div>
      )}

      {osBudgetErrorToast && (
        <div className="fixed top-20 right-4 max-w-md rounded-xl border-2 border-red-300 bg-red-50 px-4 py-3 text-red-900 shadow-xl z-[90]">
          <div className="text-sm font-bold">OS必須予算不足</div>
          <div className="mt-1 text-xs leading-relaxed">要点: {osBudgetErrorToast.point}</div>
          <div className="mt-1 text-xs font-semibold">推奨予算: {osBudgetErrorToast.recommendedBudgetText}</div>
        </div>
      )}

      {activeResult ? (
        <ResultView config={activeResult} onBack={handleBack} onSavedConfiguration={handleSavedFromResultView} />
      ) : (
        <ConfigForm onSubmit={handleGenerateConfig} isLoading={isLoading} />
      )}
    </>
  );
}

export default App;

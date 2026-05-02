import { useEffect, useMemo, useRef, useState } from "react";
import {
  getMarketPriceRange,
  getPartPriceRanges,
  getStorageInventory,
  type CustomBudgetWeights,
  type PartPriceRange,
  type StorageInventoryResponse,
} from "./api";

const FALLBACK_MARKET_PRICE_RANGE = {
  min: 89980,
  max: 404980,
  default: 250000,
};

interface ConfigFormProps {
  onSubmit: (
    budget: number,
    usage: string,
    options: {
      coolerType: "air" | "liquid";
      radiatorSize: "120" | "240" | "360";
      coolingProfile: "silent" | "performance";
      caseSize: "mini" | "mid" | "full";
      caseFanPolicy: "auto" | "silent" | "airflow";
      cpuVendor: "any" | "intel" | "amd";
      buildPriority: "cost" | "spec";
      storagePreference: "ssd" | "hdd";
      mainStorageCapacity: "512" | "1024" | "2048" | "4096";
      storage2PartId: number | null;
      storage3PartId: number | null;
      osEdition: "auto" | "home" | "pro";
      useCustomBudgetWeights: boolean;
      customBudgetWeights: CustomBudgetWeights;
    }
  ) => void;
  onCancel?: () => void;
  isLoading: boolean;
}

const DEFAULT_CUSTOM_BUDGET_WEIGHTS: CustomBudgetWeights = {
  cpu: 20,
  cpu_cooler: 2,
  gpu: 30,
  motherboard: 10,
  memory: 15,
  storage: 10,
  os: 5,
  psu: 5,
  case: 3,
};

const CUSTOM_BUDGET_WEIGHT_FIELDS: Array<{ key: keyof CustomBudgetWeights; label: string }> = [
  { key: "cpu", label: "CPU" },
  { key: "cpu_cooler", label: "CPUクーラー" },
  { key: "gpu", label: "GPU" },
  { key: "motherboard", label: "マザーボード" },
  { key: "memory", label: "メモリー" },
  { key: "storage", label: "ストレージ" },
  { key: "os", label: "OS" },
  { key: "psu", label: "PSU" },
  { key: "case", label: "ケース" },
];

const USAGE_OPTIONS = [
  { value: "gaming", label: "ゲーミングPC", icon: "🎮", desc: "GPU重視・高フレームレート向け" },
  { value: "creator", label: "クリエイターPC", icon: "🎨", desc: "動画編集・3DCG・配信向け" },
  { value: "business", label: "ビジネスPC", icon: "💼", desc: "オフィス作業・安定運用重視" },
  { value: "standard", label: "ホーム・日常用PC", icon: "🏠", desc: "日常使い・バランス型" },
  { value: "video_editing", label: "ワークステーション", icon: "⚙️", desc: "CAD・3DCG・エンジニアリング・高負荷処理向け" },
] as const;

const COOLER_OPTIONS = [
  { value: "air", label: "空冷", desc: "静音性・メンテ重視" },
  { value: "liquid", label: "水冷", desc: "高負荷時の冷却性能重視" },
] as const;

const RADIATOR_OPTIONS = [
  { value: "120", label: "120mm" },
  { value: "240", label: "240mm" },
  { value: "360", label: "360mm" },
] as const;

const COOLING_PROFILE_OPTIONS = [
  { value: "silent", label: "静音重視" },
  { value: "performance", label: "冷却重視" },
] as const;

const CASE_SIZE_OPTIONS = [
  { value: "mini", label: "コンパクト" },
  { value: "mid", label: "ミドル" },
  { value: "full", label: "フルサイズ" },
] as const;

const CASE_FAN_POLICY_OPTIONS = [
  { value: "auto", label: "自動" },
  { value: "silent", label: "静音重視" },
  { value: "airflow", label: "冷却重視" },
] as const;

const CPU_VENDOR_OPTIONS = [
  { value: "any", label: "こだわらない" },
  { value: "intel", label: "Intel" },
  { value: "amd", label: "AMD" },
] as const;

const BUILD_PRIORITY_OPTIONS = [
  { value: "cost", label: "コスト重視" },
  { value: "spec", label: "スペック重視" },
] as const;

const STORAGE_PREFERENCE_OPTIONS = [
  { value: "ssd", label: "SSD" },
] as const;

const MAIN_STORAGE_CAPACITY_OPTIONS = [
  { value: "512", label: "512GB" },
  { value: "1024", label: "1TB" },
  { value: "2048", label: "2TB" },
  { value: "4096", label: "4TB" },
] as const;

const STORAGE_ADDITIONAL_OPTIONS = [
  { value: "none", label: "なし", desc: "追加ストレージなし" },
  { value: "nvme_ssd", label: "M.2 SSD", desc: "超高速なストレージを使用" },
  { value: "sata_ssd", label: "SATA SSD", desc: "高速なストレージを使用" },
  { value: "hdd", label: "HDD", desc: "低速だが大容量でも比較的に安価" },
] as const;

const OS_EDITION_OPTIONS = [
  { value: "auto", label: "自動", desc: "用途に合わせて Home / Pro を自動選択" },
  { value: "home", label: "Home", desc: "個人利用向けの標準構成" },
  { value: "pro", label: "Pro", desc: "業務用途向けの拡張機能込み" },
] as const;

const STORAGE_INTERFACE_FILTER_OPTIONS = [
  { value: "all", label: "すべて" },
  { value: "nvme", label: "NVMe" },
  { value: "sata", label: "SATA" },
  { value: "other", label: "その他" },
] as const;

const STORAGE_MEDIA_LABELS: Record<"ssd" | "hdd" | "other", string> = {
  ssd: "SSD",
  hdd: "HDD",
  other: "不明",
};

const STORAGE_CAPACITY_PRIORITY = new Map([
  [1024, 0],
  [2048, 1],
  [4096, 2],
  [512, 3],
  [256, 4],
  [8192, 5],
  [0, 99],
]);

function getStorageItemRecommendationScore(item: StorageInventoryResponse["capacity_summary"][number]["items"][number]) {
  const interfaceScore = item.interface === "nvme" ? 300 : item.interface === "sata" ? 180 : 80;
  const formFactorScore = item.form_factor === "M.2" ? 40 : item.form_factor === "2.5inch" ? 20 : 0;
  const valueScore = Math.max(0, 200000 - item.price) / 1000;
  return interfaceScore + formFactorScore + valueScore;
}

function getStorageCapacityPriority(capacityGb: number) {
  return STORAGE_CAPACITY_PRIORITY.get(capacityGb) ?? (capacityGb >= 1024 ? 10 : 20);
}

function formatCapacityLabel(capacityGb: number) {
  if (capacityGb >= 1024) {
    const tb = capacityGb / 1024;
    return Number.isInteger(tb) ? `${tb}TB` : `${tb.toFixed(1)}TB`;
  }
  return `${capacityGb}GB`;
}

function inferStorageMediaType(item: StorageInventoryResponse["capacity_summary"][number]["items"][number]): "ssd" | "hdd" | "other" {
  const text = item.name.toLowerCase();
  const formFactor = (item.form_factor ?? "").toLowerCase();

  if (item.interface === "nvme") {
    return "ssd";
  }
  if (text.includes("ssd") || formFactor.includes("m.2") || formFactor.includes("2.5inch") || text.includes("m.2")) {
    return "ssd";
  }
  // WD SSD モデル番号
  if (/\b(sa500|sn500|sn580|sn700|sn750|sn850)\b/.test(text)) {
    return "ssd";
  }
  if (/(5400|7200|10000|15000)\s*rpm/i.test(item.name)) {
    return "hdd";
  }
  // HDD キーワード ─ "wd red" 単体は SSD モデルと被るため除外
  const hddKeywords = [
    "barracuda",
    "ironwolf",
    "wd blue wd",
    "wd green wd",
    "wd red wd",
    "wd purple wd",
    "mq04",
    "dt02",
    "n300",
    "mg10",
    "mg11",
    "hat3300",
    "hdd",
  ];
  if (hddKeywords.some((keyword) => text.includes(keyword))) {
    return "hdd";
  }
  if (item.interface === "sata" && formFactor.includes("3.5")) {
    return "hdd";
  }
  if (item.interface === "sata" && (formFactor.includes("2.5") || formFactor.includes("m.2"))) {
    return "ssd";
  }
  return "other";
}

function isAdditionalStorageOptionMatch(
  item: StorageInventoryResponse["capacity_summary"][number]["items"][number],
  preference: "none" | "nvme_ssd" | "sata_ssd" | "hdd"
) {
  if (preference === "none") {
    return false;
  }
  if (preference === "hdd") {
    return inferStorageMediaType(item) === "hdd";
  }
  if (preference === "nvme_ssd") {
    return inferStorageMediaType(item) === "ssd" && item.interface === "nvme";
  }
  if (preference === "sata_ssd") {
    return inferStorageMediaType(item) === "ssd" && item.interface === "sata";
  }
  return false;
}

function getMainStorageAnnotation(): string {
  return "デフォルトでは必ず選択されます。";
}

export function ConfigForm({ onSubmit, onCancel, isLoading }: ConfigFormProps) {
  const [marketRange, setMarketRange] = useState(FALLBACK_MARKET_PRICE_RANGE);
  const [marketRangeLoading, setMarketRangeLoading] = useState(true);
  const [marketRangeError, setMarketRangeError] = useState<string | null>(null);
  const [budget, setBudget] = useState(FALLBACK_MARKET_PRICE_RANGE.default);
  const [usage, setUsage] = useState("gaming");
  const [coolerType, setCoolerType] = useState<"air" | "liquid">("air");
  const [radiatorSize, setRadiatorSize] = useState<"120" | "240" | "360">("240");
  const [coolingProfile, setCoolingProfile] = useState<"silent" | "performance">("performance");
  const [caseSize, setCaseSize] = useState<"mini" | "mid" | "full">("mid");
  const [caseFanPolicy, setCaseFanPolicy] = useState<"auto" | "silent" | "airflow">("auto");
  const [cpuVendor, setCpuVendor] = useState<"any" | "intel" | "amd">("any");
  const [buildPriority, setBuildPriority] = useState<"cost" | "spec">("cost");
  const previousBuildPriorityRef = useRef<"cost" | "spec">("cost");
  const [storagePreference, setStoragePreference] = useState<"ssd" | "hdd">("ssd");
  const [mainStorageCapacity, setMainStorageCapacity] = useState<"512" | "1024" | "2048" | "4096">("512");
  const [storagePreference2, setStoragePreference2] = useState<"none" | "nvme_ssd" | "sata_ssd" | "hdd">("none");
  const [storagePreference3, setStoragePreference3] = useState<"none" | "nvme_ssd" | "sata_ssd" | "hdd">("none");
  const [storage2CapacityGb, setStorage2CapacityGb] = useState<number | null>(null);
  const [storage2ProductId, setStorage2ProductId] = useState<number | null>(null);
  const [storage3CapacityGb, setStorage3CapacityGb] = useState<number | null>(null);
  const [storage3ProductId, setStorage3ProductId] = useState<number | null>(null);
  const [osEdition, setOsEdition] = useState<"auto" | "home" | "pro">("auto");
  const [useCustomBudgetWeights, setUseCustomBudgetWeights] = useState(false);
  const [customBudgetWeights, setCustomBudgetWeights] = useState<CustomBudgetWeights>(DEFAULT_CUSTOM_BUDGET_WEIGHTS);
  const [gpuRange, setGpuRange] = useState<PartPriceRange | null>(null);
  const [storageInventory, setStorageInventory] = useState<StorageInventoryResponse | null>(null);
  const [storageInventoryLoading, setStorageInventoryLoading] = useState(true);
  const [storageQuery, setStorageQuery] = useState("");
  const [storageInterfaceFilter, setStorageInterfaceFilter] = useState<"all" | "nvme" | "sata" | "other">("all");
  const [showMarketSummary, setShowMarketSummary] = useState(true);
  const [showStorageDbDetails, setShowStorageDbDetails] = useState(false);
  const [popupMessage, setPopupMessage] = useState<string | null>(null);
  const [selectedPresetLabel, setSelectedPresetLabel] = useState<string | null>("ミドル");
  const [activeUsageTooltip, setActiveUsageTooltip] = useState<string | null>(null);
  const [activeCoolerTooltip, setActiveCoolerTooltip] = useState<string | null>(null);
  const budgetMin = 50000;
  const budgetMax = 1500000;

  useEffect(() => {
    const loadMarketRange = async () => {
      try {
        const range = await getMarketPriceRange();
        if (range.min > 0 && range.max >= range.min) {
          const safeDefault = Math.min(range.max, Math.max(range.min, range.default));
          setMarketRange({ min: range.min, max: range.max, default: safeDefault });
          setBudget((current) => {
            if (current < range.min || current > range.max) {
              return safeDefault;
            }
            return current;
          });
        }
        setMarketRangeError(null);
      } catch {
        setMarketRangeError("相場APIの取得に失敗したため、ローカル目安を表示しています。");
      } finally {
        setMarketRangeLoading(false);
      }
    };

    loadMarketRange();
  }, []);

  useEffect(() => {
    const loadPartRanges = async () => {
      try {
        const ranges = await getPartPriceRanges();
        if (ranges.gpu) {
          setGpuRange(ranges.gpu);
        }
      } catch {
        return;
      }
    };

    loadPartRanges();
  }, []);

  useEffect(() => {
    const loadStorageInventory = async () => {
      try {
        const inventory = await getStorageInventory();
        setStorageInventory(inventory);
      } catch {
        return;
      } finally {
        setStorageInventoryLoading(false);
      }
    };

    loadStorageInventory();
  }, []);

  const previousUsageRef = useRef<string>("");

  useEffect(() => {
    if (previousUsageRef.current === usage) {
      return;
    }
    previousUsageRef.current = usage;

    // 用途切り替え時にミドルプリセットをデフォルト予算として設定
    const applyPriority = (v: number) =>
      Math.max(0, Math.min(budgetMax, buildPriority === "spec" ? Math.round(v * 1.1) : v));

    // business は静的プリセット（sub引き算なし）、その他は base - 15000
    const middleValues: Record<string, number> = {
      gaming: 274980 - 15000,       // 259,980
      creator: 299980 - 15000,      // 284,980
      video_editing: 299980 - 15000,// 284,980
      business: 114980,             // 静的プリセットのまま
      standard: 109980 - 15000,     // 94,980
    };
    const middleValue = applyPriority(middleValues[usage] ?? 299980 - 15000);
    setBudget(middleValue);
    setSelectedPresetLabel("ミドル");
  }, [usage, budgetMax, buildPriority]);

  useEffect(() => {
    const prev = previousBuildPriorityRef.current;
    if (prev === buildPriority) {
      return;
    }

    setBudget((current) => {
      const clampedCurrent = Math.min(budgetMax, Math.max(budgetMin, current));
      if (prev === "cost" && buildPriority === "spec") {
        return Math.min(budgetMax, Math.round(clampedCurrent * 1.1));
      }
      if (prev === "spec" && buildPriority === "cost") {
        return Math.max(budgetMin, Math.round(clampedCurrent / 1.1));
      }
      return clampedCurrent;
    });

    previousBuildPriorityRef.current = buildPriority;
  }, [buildPriority, budgetMax, budgetMin]);

  useEffect(() => {
    if (!popupMessage) {
      return;
    }

    const timer = window.setTimeout(() => {
      setPopupMessage(null);
    }, 2200);

    return () => window.clearTimeout(timer);
  }, [popupMessage]);

  const getEffectiveBudgetByPriority = (rawBudget: number) => {
    const clamped = Math.min(budgetMax, Math.max(budgetMin, rawBudget));
    return clamped;
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const nativeEvent = event.nativeEvent as SubmitEvent;
    const submitter = nativeEvent.submitter as HTMLElement | null;
    if (submitter && submitter.getAttribute("data-role") !== "primary-submit") {
      return;
    }
    const effectiveBudget = getEffectiveBudgetByPriority(budget);
    onSubmit(effectiveBudget, usage, {
      coolerType,
      radiatorSize,
      coolingProfile,
      caseSize,
      caseFanPolicy,
      cpuVendor,
      buildPriority,
      storagePreference,
      mainStorageCapacity,
      storage2PartId: storage2ProductId,
      storage3PartId: storage3ProductId,
      osEdition,
      useCustomBudgetWeights,
      customBudgetWeights,
    });
  };

  const customBudgetWeightTotal = useMemo(
    () => Object.values(customBudgetWeights).reduce((sum, value) => sum + value, 0),
    [customBudgetWeights]
  );

  const presets = useMemo(() => {
    const min = marketRange.min;
    const sub = 15000;
    const applyPriorityPremium = (value: number) => {
      const adjusted = buildPriority === "spec" ? Math.round(value * 1.1) : value;
      return Math.max(0, Math.min(budgetMax, adjusted));
    };

    const toPresetValues = (baseValues: number[]) =>
      baseValues.map((price) => applyPriorityPremium(price - sub));

    if (usage === "gaming") {
      const bases = [179980, 274980, 514980, 999980].map((value) => Math.min(budgetMax, value));
      const [entry, middle, high, flagship] = toPresetValues(bases);
      return [
        { label: "ローエンド", value: entry },
        { label: "ミドル", value: middle },
        { label: "ハイエンド", value: high },
        { label: "プレミアム", value: flagship },
      ];
    }

    if (usage === "standard") {
      const bases = [89980, 109980, 172980, 249980];
      const [entry, middle, high, flagship] = toPresetValues(bases);
      return [
        { label: "ローエンド", value: entry },
        { label: "ミドル", value: middle },
        { label: "ハイエンド", value: high },
        { label: "プレミアム", value: flagship },
      ];
    }

    if (usage === "business") {
      const costBases = [99980, 114980, 134980, 159980];
      const [entry, middle, high, flagship] = costBases.map((value) => applyPriorityPremium(value));
      return [
        { label: "ローエンド", value: entry },
        { label: "ミドル", value: middle },
        { label: "ハイエンド", value: high },
        { label: "プレミアム", value: flagship },
      ];
    }

    const creatorBases = [184980, 299980, 449980, 699980];
    const [entry, middle, high, flagship] = toPresetValues(creatorBases);

    return [
      { label: "ローエンド", value: entry },
      { label: "ミドル", value: middle },
      { label: "ハイエンド", value: high },
      { label: "プレミアム", value: flagship },
    ];
  }, [budgetMax, buildPriority, marketRange.min, usage]);

  const usagePriceHint = useMemo(() => {
    if (presets.length === 0) {
      return null;
    }
    const min = Math.min(...presets.map((preset) => preset.value));
    const max = Math.max(...presets.map((preset) => preset.value));
    return { min, max };
  }, [presets]);

  const budgetProgress = useMemo(() => {
    const range = budgetMax - budgetMin;
    if (range <= 0) {
      return 0;
    }
    return Math.max(0, Math.min(100, ((budget - budgetMin) / range) * 100));
  }, [budget, budgetMax, budgetMin]);

  const budgetDigits = useMemo(() => {
    const safeBudget = Math.max(0, Number.isFinite(budget) ? budget : 0);
    return String(Math.trunc(safeBudget)).length;
  }, [budget]);

  const yenSymbolLeft = useMemo(() => {
    return `calc(50% - ${Math.max(2, budgetDigits) * 0.4}ch - 1.35em)`;
  }, [budgetDigits]);

  const canSubmit = !isLoading && (!useCustomBudgetWeights || customBudgetWeightTotal > 0);

  const compactCapacityGroups = useMemo(() => {
    const normalizedQuery = storageQuery.trim().toLowerCase();
    const groups = storageInventory?.capacity_summary ?? [];

    return groups
      .map((group) => {
        const items = group.items
          .filter((item) => {
            if (storageInterfaceFilter !== "all" && item.interface !== storageInterfaceFilter) {
              return false;
            }
            if (!normalizedQuery) {
              return true;
            }
            const target = [item.name, item.interface_label, item.form_factor ?? "", group.label]
              .join(" ")
              .toLowerCase();
            return target.includes(normalizedQuery);
          })
          .sort((left, right) => {
            const scoreDiff = getStorageItemRecommendationScore(right) - getStorageItemRecommendationScore(left);
            if (scoreDiff !== 0) {
              return scoreDiff;
            }
            return left.price - right.price;
          });

        const prices = items.map((item) => item.price);

        return {
          ...group,
          count: items.length,
          min_price: prices.length > 0 ? Math.min(...prices) : null,
          max_price: prices.length > 0 ? Math.max(...prices) : null,
          avg_price: prices.length > 0 ? Math.round(prices.reduce((sum, price) => sum + price, 0) / prices.length) : null,
          items,
        };
      })
      .filter((group) => group.count > 0)
      .sort((left, right) => {
        const priorityDiff = getStorageCapacityPriority(left.capacity_gb) - getStorageCapacityPriority(right.capacity_gb);
        if (priorityDiff !== 0) {
          return priorityDiff;
        }
        return left.capacity_gb - right.capacity_gb;
      });
  }, [storageInterfaceFilter, storageInventory, storageQuery]);

  const interfaceSummary = useMemo(
    () => storageInventory?.interface_summary.filter((group) => group.count > 0) ?? [],
    [storageInventory]
  );

  const filteredStorageCount = useMemo(
    () => compactCapacityGroups.reduce((sum, group) => sum + group.count, 0),
    [compactCapacityGroups]
  );

  const segmentButtonClass = (selected: boolean) =>
    `rounded-lg border px-3 py-2 text-sm font-medium transition ${
      selected
        ? "border-blue-700 bg-blue-700 text-white"
        : "border-slate-300 bg-white text-slate-800 hover:bg-slate-50"
    }`;

  const storageInventoryItems = useMemo(
    () => storageInventory?.capacity_summary.flatMap((group) => group.items) ?? [],
    [storageInventory]
  );

  const storage2CapacityOptions = useMemo(() => {
    if (storagePreference2 === "none") {
      return [] as Array<{ capacityGb: number; label: string }>;
    }
    const capacities = Array.from(
      new Set(
        storageInventoryItems
          .filter((item) => isAdditionalStorageOptionMatch(item, storagePreference2))
          .map((item) => item.capacity_gb)
      )
    ).sort((a, b) => a - b);
    return capacities.map((capacityGb) => ({
      capacityGb,
      label: formatCapacityLabel(capacityGb),
    }));
  }, [storageInventoryItems, storagePreference2]);

  const storage3CapacityOptions = useMemo(() => {
    if (storagePreference3 === "none") {
      return [] as Array<{ capacityGb: number; label: string }>;
    }
    const capacities = Array.from(
      new Set(
        storageInventoryItems
          .filter((item) => isAdditionalStorageOptionMatch(item, storagePreference3))
          .map((item) => item.capacity_gb)
      )
    ).sort((a, b) => a - b);
    return capacities.map((capacityGb) => ({
      capacityGb,
      label: formatCapacityLabel(capacityGb),
    }));
  }, [storageInventoryItems, storagePreference3]);

  const storage2ProductOptions = useMemo(() => {
    if (storagePreference2 === "none" || storage2CapacityGb == null) {
      return [] as typeof storageInventoryItems;
    }
    return storageInventoryItems
      .filter((item) => isAdditionalStorageOptionMatch(item, storagePreference2))
      .filter((item) => item.capacity_gb === storage2CapacityGb)
      .sort((a, b) => a.price - b.price);
  }, [storageInventoryItems, storagePreference2, storage2CapacityGb]);

  const storage3ProductOptions = useMemo(() => {
    if (storagePreference3 === "none" || storage3CapacityGb == null) {
      return [] as typeof storageInventoryItems;
    }
    return storageInventoryItems
      .filter((item) => isAdditionalStorageOptionMatch(item, storagePreference3))
      .filter((item) => item.capacity_gb === storage3CapacityGb)
      .sort((a, b) => a.price - b.price);
  }, [storageInventoryItems, storagePreference3, storage3CapacityGb]);

  useEffect(() => {
    if (storagePreference2 === "none") {
      setStorage2CapacityGb(null);
      setStorage2ProductId(null);
      return;
    }
    if (!storage2CapacityOptions.some((option) => option.capacityGb === storage2CapacityGb)) {
      setStorage2CapacityGb(storage2CapacityOptions[0]?.capacityGb ?? null);
    }
  }, [storagePreference2, storage2CapacityGb, storage2CapacityOptions]);

  useEffect(() => {
    if (storage2ProductOptions.length === 0) {
      setStorage2ProductId(null);
      return;
    }
    if (!storage2ProductOptions.some((item) => item.id === storage2ProductId)) {
      setStorage2ProductId(storage2ProductOptions[0].id);
    }
  }, [storage2ProductId, storage2ProductOptions]);

  useEffect(() => {
    if (storagePreference3 === "none") {
      setStorage3CapacityGb(null);
      setStorage3ProductId(null);
      return;
    }
    if (!storage3CapacityOptions.some((option) => option.capacityGb === storage3CapacityGb)) {
      setStorage3CapacityGb(storage3CapacityOptions[0]?.capacityGb ?? null);
    }
  }, [storagePreference3, storage3CapacityGb, storage3CapacityOptions]);

  useEffect(() => {
    if (storage3ProductOptions.length === 0) {
      setStorage3ProductId(null);
      return;
    }
    if (!storage3ProductOptions.some((item) => item.id === storage3ProductId)) {
      setStorage3ProductId(storage3ProductOptions[0].id);
    }
  }, [storage3ProductId, storage3ProductOptions]);

  return (
    <div className="min-h-screen bg-slate-100 px-4 py-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <form id="config-form" onSubmit={handleSubmit} className="space-y-4 rounded-xl border border-slate-300 bg-white p-5 pb-28">
          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-slate-900">予算</h2>
              <label className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-700">
                <input
                  type="checkbox"
                  checked={showMarketSummary}
                  onChange={(e) => setShowMarketSummary(e.target.checked)}
                />
                相場目安の表示
              </label>
            </div>
            {showMarketSummary && (
              <>
                <p className="text-sm text-slate-600">予算と用途を選ぶと、条件に沿った構成を提案します。</p>
                <div className="grid gap-2 text-sm">
                  {usagePriceHint && (
                    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                      用途別の推奨予算帯: <span className="font-semibold text-slate-900">{`¥${usagePriceHint.min.toLocaleString("ja-JP")} - ¥${usagePriceHint.max.toLocaleString("ja-JP")}`}</span>
                    </div>
                  )}
                  {marketRangeError && <p className="text-xs text-amber-700">{marketRangeError}</p>}
                </div>
              </>
            )}
            <div>
              <input
                type="range"
                aria-label="予算スライダー"
                min={budgetMin}
                max={budgetMax}
                step={1000}
                value={Math.min(budgetMax, Math.max(budgetMin, budget))}
                onChange={(event) => {
                  setBudget(Number(event.target.value));
                  setSelectedPresetLabel(null);
                }}
                className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200 accent-blue-600"
                style={{ backgroundSize: `${budgetProgress}% 100%` }}
              />
              <div className="mt-1 flex items-center justify-between text-xs text-slate-500">
                <span>{`¥${budgetMin.toLocaleString("ja-JP")}`}</span>
                <span className="font-semibold text-slate-700">{`現在: ¥${Math.min(budgetMax, Math.max(budgetMin, budget)).toLocaleString("ja-JP")}`}</span>
                <span>{`¥${budgetMax.toLocaleString("ja-JP")}`}</span>
              </div>
            </div>
            <div className="relative">
              <span
                className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-lg font-semibold text-slate-500"
                style={{ left: yenSymbolLeft }}
              >
                ￥
              </span>
              <input
                type="number"
                value={budget}
                onFocus={() => setPopupMessage(`入力範囲: ¥${budgetMin.toLocaleString("ja-JP")} - ¥${budgetMax.toLocaleString("ja-JP")}`)}
                onChange={(e) => {
                  setBudget(Number(e.target.value));
                  setSelectedPresetLabel(null);
                  setPopupMessage(`入力範囲: ¥${budgetMin.toLocaleString("ja-JP")} - ¥${budgetMax.toLocaleString("ja-JP")}`);
                }}
                min={50000}
                max={1500000}
                step={1}
                className="w-full rounded-lg border border-slate-300 py-2 pl-6 pr-3 text-center text-lg font-semibold text-slate-900 outline-none focus:border-blue-600"
              />
            </div>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-4">
            <h2 className="text-base font-semibold text-slate-900">用途</h2>
            <div className="grid gap-2 sm:grid-cols-2">
              {USAGE_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={`relative rounded-lg border p-3 ${usage === option.value ? "border-blue-700 bg-blue-50" : "border-slate-300"}`}
                  onMouseEnter={() => setActiveUsageTooltip(option.value)}
                  onMouseLeave={() => setActiveUsageTooltip((current) => (current === option.value ? null : current))}
                >
                  <input
                    type="radio"
                    name="usage"
                    value={option.value}
                    checked={usage === option.value}
                    onChange={(e) => {
                      const nextUsage = e.target.value;
                      setUsage(nextUsage);
                    }}
                    onFocus={() => setActiveUsageTooltip(option.value)}
                    onBlur={() => setActiveUsageTooltip((current) => (current === option.value ? null : current))}
                    className="mr-2"
                  />
                  <span className="font-medium text-slate-900">{option.icon} {option.label}</span>
                  {activeUsageTooltip === option.value && (
                    <span className="pointer-events-none absolute -top-11 left-1/2 z-30 w-max max-w-[90%] -translate-x-1/2 rounded-md border border-blue-200 bg-white px-3 py-1 text-xs font-medium text-blue-800 shadow-md">
                      {option.desc}
                    </span>
                  )}
                </label>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {presets.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  onClick={() => {
                    setBudget(preset.value);
                    setSelectedPresetLabel(preset.label);
                  }}
                  className={segmentButtonClass(selectedPresetLabel === preset.label)}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-4">
            <h2 className="text-base font-semibold text-slate-900">CPU</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="mb-2 text-sm font-medium text-slate-800">CPUメーカー</p>
                <div className="grid grid-cols-3 gap-2">
                  {CPU_VENDOR_OPTIONS.map((option) => (
                    <button key={option.value} type="button" onClick={() => setCpuVendor(option.value as "any" | "intel" | "amd")} className={segmentButtonClass(cpuVendor === option.value)}>
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-2 text-sm font-medium text-slate-800">ビルド優先度</p>
                <div className="grid grid-cols-2 gap-2">
                  {BUILD_PRIORITY_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      disabled={isLoading || (useCustomBudgetWeights && customBudgetWeightTotal <= 0)}
                      onClick={() => setBuildPriority(option.value as "cost" | "spec")}
                      className={`${segmentButtonClass(buildPriority === option.value)} disabled:cursor-not-allowed disabled:opacity-50`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-xs text-slate-500">スペック重視に切り替えると表示予算を10%上乗せします。</p>
              </div>
            </div>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-4">
            <h2 className="text-base font-semibold text-slate-900">ストレージ</h2>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="h-full rounded-lg border border-slate-200 bg-white p-3">
                  <p className="mb-2 text-sm font-semibold text-slate-800">メインストレージ</p>
                  <div className="grid gap-2 grid-cols-3">
                    {MAIN_STORAGE_CAPACITY_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setMainStorageCapacity(option.value as "512" | "1024" | "2048" | "4096")}
                        className={segmentButtonClass(mainStorageCapacity === option.value)}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="h-full rounded-lg border border-slate-200 bg-white p-3">
                  <p className="mb-2 text-sm font-semibold text-slate-800">ストレージ2</p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {STORAGE_ADDITIONAL_OPTIONS.map((option) => (
                      <button
                        key={`storage2-${option.value}`}
                        type="button"
                        onClick={() => {
                          setStoragePreference2(option.value as "none" | "nvme_ssd" | "sata_ssd" | "hdd");
                          setStorage2CapacityGb(null);
                          setStorage2ProductId(null);
                        }}
                        title={option.desc}
                        className={`${segmentButtonClass(storagePreference2 === option.value)} group relative`}
                      >
                        <span className="block">{option.label}</span>
                        <span className="pointer-events-none absolute -top-12 left-1/2 z-30 w-64 -translate-x-1/2 rounded-md border border-blue-200 bg-white px-2 py-1 text-[11px] font-normal text-blue-800 opacity-0 shadow-md transition group-hover:opacity-100 group-focus-visible:opacity-100">
                          {option.desc}
                        </span>
                      </button>
                    ))}
                  </div>
                  {storagePreference2 !== "none" && (
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      <select
                        value={storage2CapacityGb ?? ""}
                        onChange={(event) => {
                          const value = Number(event.target.value);
                          setStorage2CapacityGb(Number.isFinite(value) ? value : null);
                          setStorage2ProductId(null);
                        }}
                        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-600"
                      >
                        <option value="">容量を選択</option>
                        {storage2CapacityOptions.map((option) => (
                          <option key={`storage2-capacity-${option.capacityGb}`} value={option.capacityGb}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      <select
                        value={storage2ProductId ?? ""}
                        onChange={(event) => {
                          const value = Number(event.target.value);
                          setStorage2ProductId(Number.isFinite(value) ? value : null);
                        }}
                        disabled={storage2CapacityGb == null || storage2ProductOptions.length === 0}
                        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-600 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500"
                      >
                        <option value="">製品を選択</option>
                        {storage2ProductOptions.map((item) => (
                          <option key={`storage2-item-${item.id}`} value={item.id}>
                            {`${item.name} / ¥${item.price.toLocaleString("ja-JP")}`}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>

                <div className="h-full rounded-lg border border-slate-200 bg-white p-3">
                  <p className="mb-2 text-sm font-semibold text-slate-800">ストレージ3</p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {STORAGE_ADDITIONAL_OPTIONS.map((option) => (
                      <button
                        key={`storage3-${option.value}`}
                        type="button"
                        onClick={() => {
                          setStoragePreference3(option.value as "none" | "nvme_ssd" | "sata_ssd" | "hdd");
                          setStorage3CapacityGb(null);
                          setStorage3ProductId(null);
                        }}
                        title={option.desc}
                        className={`${segmentButtonClass(storagePreference3 === option.value)} group relative`}
                      >
                        <span className="block">{option.label}</span>
                        <span className="pointer-events-none absolute -top-12 left-1/2 z-30 w-64 -translate-x-1/2 rounded-md border border-blue-200 bg-white px-2 py-1 text-[11px] font-normal text-blue-800 opacity-0 shadow-md transition group-hover:opacity-100 group-focus-visible:opacity-100">
                          {option.desc}
                        </span>
                      </button>
                    ))}
                  </div>
                  {storagePreference3 !== "none" && (
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      <select
                        value={storage3CapacityGb ?? ""}
                        onChange={(event) => {
                          const value = Number(event.target.value);
                          setStorage3CapacityGb(Number.isFinite(value) ? value : null);
                          setStorage3ProductId(null);
                        }}
                        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-600"
                      >
                        <option value="">容量を選択</option>
                        {storage3CapacityOptions.map((option) => (
                          <option key={`storage3-capacity-${option.capacityGb}`} value={option.capacityGb}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      <select
                        value={storage3ProductId ?? ""}
                        onChange={(event) => {
                          const value = Number(event.target.value);
                          setStorage3ProductId(Number.isFinite(value) ? value : null);
                        }}
                        disabled={storage3CapacityGb == null || storage3ProductOptions.length === 0}
                        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-600 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500"
                      >
                        <option value="">製品を選択</option>
                        {storage3ProductOptions.map((item) => (
                          <option key={`storage3-item-${item.id}`} value={item.id}>
                            {`${item.name} / ¥${item.price.toLocaleString("ja-JP")}`}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
              </div>

              <p className="mt-3 text-xs text-slate-500">
                各選択肢にカーソルを合わせると、用途仕様の注釈を表示します。
              </p>
            </div>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-4">
            <h2 className="text-base font-semibold text-slate-900">冷却・ケース</h2>

            <div className="space-y-2">
              <p className="text-sm font-medium text-slate-800">CPUクーラー方式</p>
              {COOLER_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={`relative block rounded-lg border p-3 ${coolerType === option.value ? "border-blue-700 bg-blue-50" : "border-slate-300"}`}
                  onMouseEnter={() => setActiveCoolerTooltip(option.value)}
                  onMouseLeave={() => setActiveCoolerTooltip((current) => (current === option.value ? null : current))}
                >
                  <input
                    type="radio"
                    name="coolerType"
                    value={option.value}
                    checked={coolerType === option.value}
                    onChange={(e) => setCoolerType(e.target.value as "air" | "liquid")}
                    onFocus={() => setActiveCoolerTooltip(option.value)}
                    onBlur={() => setActiveCoolerTooltip((current) => (current === option.value ? null : current))}
                    className="mr-2"
                  />
                  <span className="font-medium text-slate-900">{option.label}</span>
                  {activeCoolerTooltip === option.value && (
                    <span className="pointer-events-none absolute -top-11 left-1/2 z-30 w-max max-w-[90%] -translate-x-1/2 rounded-md border border-blue-200 bg-white px-3 py-1 text-xs font-medium text-blue-800 shadow-md">
                      {option.desc}
                    </span>
                  )}
                </label>
              ))}
            </div>

            {coolerType === "liquid" && (
              <div className="space-y-2 rounded-lg border border-rose-200 bg-rose-50 p-3">
                <p className="text-xs text-rose-700">水漏れ時の保証はクーラー単体のみで、他パーツは対象外になる可能性があります。</p>
                <div className="grid grid-cols-3 gap-2">
                  {RADIATOR_OPTIONS.map((option) => (
                    <button key={option.value} type="button" onClick={() => setRadiatorSize(option.value as "120" | "240" | "360")} className={segmentButtonClass(radiatorSize === option.value)}>
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <p className="mb-2 text-sm font-medium text-slate-800">クーラー方針</p>
                <div className="grid grid-cols-2 gap-2">
                  {COOLING_PROFILE_OPTIONS.map((option) => (
                    <button key={option.value} type="button" onClick={() => setCoolingProfile(option.value as "silent" | "performance")} className={segmentButtonClass(coolingProfile === option.value)}>
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-2 text-sm font-medium text-slate-800">ケースサイズ</p>
                <div className="grid grid-cols-3 gap-2">
                  {CASE_SIZE_OPTIONS.map((option) => (
                    <button key={option.value} type="button" onClick={() => setCaseSize(option.value as "mini" | "mid" | "full")} className={segmentButtonClass(caseSize === option.value)}>
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-2 text-sm font-medium text-slate-800">ケースファン方針</p>
                <div className="grid grid-cols-3 gap-2">
                  {CASE_FAN_POLICY_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setCaseFanPolicy(option.value as "auto" | "silent" | "airflow")}
                      className={segmentButtonClass(caseFanPolicy === option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {(caseSize === "mini" || caseSize === "mid") && (
              <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                コンパクト / ミドルケースでは、CPUクーラー高とラジエーター対応寸法を確認してください。
              </p>
            )}
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-4">
            <h2 className="text-base font-semibold text-slate-900">OS</h2>
            <div className="grid gap-2 sm:grid-cols-3">
              {OS_EDITION_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setOsEdition(option.value as "auto" | "home" | "pro")}
                  className={segmentButtonClass(osEdition === option.value)}
                >
                  <span className="block">{option.label}</span>
                  <span className="mt-1 block text-[11px] font-normal opacity-80">{option.desc}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-4">
            <h2 className="text-base font-semibold text-slate-900">予算配分</h2>
            <label className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <input type="checkbox" checked={useCustomBudgetWeights} onChange={(e) => setUseCustomBudgetWeights(e.target.checked)} />
              カスタム予算配分を使う
            </label>

            {useCustomBudgetWeights && (
              <>
                <div className="mx-auto grid w-full max-w-2xl gap-3 sm:grid-cols-2">
                  {CUSTOM_BUDGET_WEIGHT_FIELDS.map((field) => (
                    <label key={field.key} className="mx-auto flex w-full max-w-xs items-center justify-between rounded-lg border border-slate-300 p-3 text-sm text-slate-700">
                      <span className="font-medium">{field.label}</span>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          step={1}
                          value={customBudgetWeights[field.key]}
                          onChange={(e) => {
                            const nextValue = Number(e.target.value);
                            setCustomBudgetWeights((current) => ({
                              ...current,
                              [field.key]: Number.isFinite(nextValue) ? nextValue : 0,
                            }));
                          }}
                          className="w-20 rounded-lg border border-slate-300 px-2 py-1 text-center text-sm text-slate-900 outline-none focus:border-blue-600"
                        />
                        <span className="text-slate-600">%</span>
                      </div>
                    </label>
                  ))}
                </div>
                <p className={`text-center text-sm font-semibold ${customBudgetWeightTotal === 100 ? "text-emerald-700" : "text-rose-700"}`}>
                  合計: {customBudgetWeightTotal}%
                </p>
              </>
            )}
          </section>

          <p className="text-center text-xs text-slate-500">
            全パーツの互換性を確認しながら、条件に沿った構成を提案します。
          </p>
        </form>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto w-full max-w-5xl p-3 md:p-4">
          <button
            type="submit"
            form="config-form"
            data-role="primary-submit"
            disabled={!canSubmit}
            className={`w-full rounded-lg px-4 py-3 text-base font-semibold transition ${
              canSubmit
                ? "bg-blue-700 text-white hover:bg-blue-800"
                : "cursor-not-allowed bg-slate-300 text-slate-600"
            }`}
          >
            {isLoading ? "構成を生成中..." : "PC構成を提案してもらう"}
          </button>
          {isLoading && onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="mt-2 w-full rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-100"
            >
              キャンセル
            </button>
          )}
        </div>
      </div>

      {popupMessage && (
        <div className="fixed top-4 left-1/2 z-[70] -translate-x-1/2 rounded-lg border border-blue-200 bg-white px-4 py-2 text-sm font-medium text-blue-800 shadow-lg">
          {popupMessage}
        </div>
      )}
    </div>
  );
}
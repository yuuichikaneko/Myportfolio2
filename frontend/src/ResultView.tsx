import { useEffect, useMemo, useState } from "react";
import {
  createSavedConfiguration,
  compareCpuSelectionMaterial,
  compareGpuPerformance,
  CpuSelectionEntryResponse,
  CpuSelectionMaterialCompareResponse,
  CpuSelectionMaterialLatestResponse,
  getSavedConfigurationById,
  getPartsByType,
  getLatestGpuPerformance,
  getLatestCpuSelectionMaterial,
  GenerateConfigResponse,
  GpuPerformanceCompareResponse,
  GpuPerformanceEntryResponse,
  GpuPerformanceLatestResponse,
  SavedConfigurationResponse,
  SavedPartResponse,
} from "./api";
import { normalizeUsageCode } from "./usageUtils";

interface ResultProps {
  config: GenerateConfigResponse | SavedConfigurationResponse;
  onBack: () => void;
  onSavedConfiguration?: (saved: SavedConfigurationResponse) => void;
}

interface NormalizedResultPart {
  category: string;
  partType?: string;
  partId?: number | null;
  name: string;
  price: number;
  url: string;
  specs: Record<string, unknown> | null;
  isPlaceholder?: boolean;
}

interface CompatibilityCheckResult {
  ok: boolean;
  reasons: string[];
}

interface PendingIncompatibleSelection {
  category: string;
  candidate: SavedPartResponse;
  reasons: string[];
}

const PART_DISPLAY_ORDER = [
  "cpu",
  "cpu_cooler",
  "gpu",
  "motherboard",
  "memory",
  "storage",
  "storage2",
  "storage3",
  "os",
  "psu",
  "case",
] as const;

const EDITABLE_PART_CATEGORIES = new Set(PART_DISPLAY_ORDER);
const CANDIDATE_COLLAPSE_LIMIT = 12;

const STORAGE_MEDIA_LABELS: Record<"ssd" | "hdd" | "other", string> = {
  ssd: "SSD",
  hdd: "HDD",
  other: "不明",
};

const STORAGE_INTERFACE_LABELS: Record<"nvme" | "sata" | "other", string> = {
  nvme: "NVMe",
  sata: "SATA",
  other: "接続方式不明",
};

const GPU_POWER_RULES: Array<[RegExp, number]> = [
  [/rtx\s*5090/i, 575],
  [/rtx\s*5080/i, 360],
  [/rtx\s*5070\s*ti/i, 300],
  [/rtx\s*5070/i, 250],
  [/rtx\s*5060\s*ti/i, 180],
  [/rtx\s*5060/i, 150],
  [/rtx\s*5050/i, 130],
  [/rtx\s*3050/i, 70],
  [/rx\s*9070\s*xt/i, 320],
  [/rx\s*9070/i, 260],
  [/rx\s*9060\s*xt/i, 190],
  [/rx\s*6400/i, 55],
  [/arc\s*b580/i, 190],
  [/arc\s*b570/i, 150],
  [/arc\s*a310/i, 50],
];

function extractGpuModelKey(text: string) {
  const normalized = text.replace(/\s+/g, " ").trim();
  const patterns = [
    /RTX\s*\d{4}\s*Ti\s*SUPER/i,
    /RTX\s*\d{4}\s*SUPER/i,
    /RTX\s*\d{4}\s*Ti/i,
    /RTX\s*\d{4}/i,
    /GTX\s*\d{3,4}\s*Ti/i,
    /GTX\s*\d{3,4}/i,
    /GT\s*\d{3,4}/i,
    /RX\s*\d{4}\s*XTX/i,
    /RX\s*\d{4}\s*XT/i,
    /RX\s*\d{4}\s*GRE/i,
    /RX\s*\d{4}/i,
    /Intel\s+Arc\s+[AB]\d{3,4}/i,
    /Arc\s+[AB]\d{3,4}/i,
  ];

  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (match) {
      return match[0].replace(/\s+/g, " ").trim().toUpperCase();
    }
  }

  return null;
}

function normalizeGpuModelKey(text: string) {
  return text.replace(/[^A-Z0-9]+/g, "").toUpperCase();
}

function formatGpuModelLabel(entry: GpuPerformanceEntryResponse) {
  const vramLabel = entry.vram_gb ? ` ${entry.vram_gb}GB` : "";
  return `${entry.model_key}${vramLabel}`;
}

function extractCpuModelKey(text: string) {
  const normalized = text.replace(/\s+/g, " ").trim();
  const patterns = [
    /Ryzen\s+[3579]\s+\d{4}[A-Z0-9]*/i,
    /Core\s+Ultra\s+[3579]\s+\d{3}[A-Z]*/i,
    /Core\s+i[3579]\s*-?\s*\d{4,5}[A-Z]*/i,
    /Pentium\s+G\d{3,4}[A-Z]*/i,
    /Celeron\s+G\d{3,4}[A-Z]*/i,
  ];

  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (match) {
      return match[0].replace(/\s+/g, " ").trim().toUpperCase();
    }
  }

  return null;
}

function normalizeCpuModelKey(text: string) {
  return text.replace(/[^A-Z0-9]+/g, "").toUpperCase();
}

function formatCpuModelLabel(entry: CpuSelectionEntryResponse) {
  return entry.model_name;
}

const GAMING_CPU_EXCLUDED_MODELS = new Set([
  "RYZEN 5 7500F",
  "RYZEN 5 9500F",
  "RYZEN 7 8700G",
  "RYZEN 9 9900X",
  "RYZEN 9 9900X3D",
  "RYZEN 9 9950X",
  "RYZEN 9 9950X3D",
]);

function isGamingCpuX3dModel(modelName: string) {
  return /x3d/i.test(modelName);
}

const RANKING_DISPLAY_LIMIT = 5;

function sortGamingCpuEntries(entries: CpuSelectionEntryResponse[], mode: "cost" | "spec") {
  const sortedEntries = entries
    .slice()
    .filter((entry) => entry.vendor.toLowerCase() === "amd")
    .filter((entry) => !GAMING_CPU_EXCLUDED_MODELS.has(entry.model_name.replace(/\s+/g, " ").trim().toUpperCase()))
    .sort((left, right) => {
      if (mode === "cost") {
        const leftRank = left.cost_rank ?? Number.MAX_SAFE_INTEGER;
        const rightRank = right.cost_rank ?? Number.MAX_SAFE_INTEGER;

        if (leftRank !== rightRank) {
          return leftRank - rightRank;
        }

        const leftValue = left.value_score ?? (left.price && left.price > 0 ? left.perf_score / left.price : 0);
        const rightValue = right.value_score ?? (right.price && right.price > 0 ? right.perf_score / right.price : 0);

        if (rightValue !== leftValue) {
          return rightValue - leftValue;
        }

        if (right.perf_score !== left.perf_score) {
          return right.perf_score - left.perf_score;
        }

        return (left.price ?? Number.MAX_SAFE_INTEGER) - (right.price ?? Number.MAX_SAFE_INTEGER);
      }

      const leftIsX3d = isGamingCpuX3dModel(left.model_name);
      const rightIsX3d = isGamingCpuX3dModel(right.model_name);

      if (leftIsX3d !== rightIsX3d) {
        return Number(rightIsX3d) - Number(leftIsX3d);
      }

      if (right.perf_score !== left.perf_score) {
        return right.perf_score - left.perf_score;
      }

      return left.model_name.localeCompare(right.model_name, "ja");
    });

  return sortedEntries.slice(0, RANKING_DISPLAY_LIMIT);
}

function normalizeSpecText(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  return value.replace(/\s+/g, " ").trim().toLowerCase();
}

function inferManufacturerName(part: { name: string; specs?: Record<string, unknown> | null }): string {
  const makerFromSpecs = part.specs?.maker ?? part.specs?.manufacturer;
  if (typeof makerFromSpecs === "string" && makerFromSpecs.trim()) {
    return makerFromSpecs.trim();
  }
  const firstToken = part.name.trim().split(/\s+/)[0];
  return firstToken ?? "不明";
}

function parseNumberLike(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const match = value.replace(/,/g, "").match(/(\d+(?:\.\d+)?)/);
    if (match) {
      return Number(match[1]);
    }
  }
  return null;
}

function normalizeFormFactorToken(value: string): string {
  const text = value.toLowerCase().replace(/\s+/g, "").replace(/_/g, "-");
  if (text.includes("eatx") || text.includes("e-atx") || text.includes("extendedatx")) {
    return "eatx";
  }
  if (text.includes("mini-itx") || text.includes("miniitx") || text.includes("itx")) {
    return "mini-itx";
  }
  if (text.includes("micro-atx") || text.includes("microatx") || text.includes("matx") || text.includes("m-atx")) {
    return "micro-atx";
  }
  if (text.includes("atx")) {
    return "atx";
  }
  if (text.includes("sfx-l") || text.includes("sfxl")) {
    return "sfx-l";
  }
  if (text.includes("sfx")) {
    return "sfx";
  }
  return text;
}

function getPartFormFactor(part: { name: string; specs?: Record<string, unknown> | null }): string {
  const fromSpecs = part.specs?.form_factor;
  if (typeof fromSpecs === "string" && fromSpecs.trim()) {
    return normalizeFormFactorToken(fromSpecs);
  }
  return normalizeFormFactorToken(part.name);
}

function getCaseMaxCoolerHeightMm(part: { name: string; specs?: Record<string, unknown> | null }): number | null {
  const keys = [
    "max_cpu_cooler_height_mm",
    "cpu_cooler_clearance_mm",
    "max_cooler_height_mm",
    "cooler_height_limit_mm",
  ];
  for (const key of keys) {
    const parsed = parseNumberLike(part.specs?.[key]);
    if (parsed && parsed > 0) {
      return parsed;
    }
  }
  const match = part.name.match(/(?:クーラー高|cooler\s*height|max\s*cooler)\D{0,8}(\d{2,3})\s*mm/i);
  if (match) {
    return Number(match[1]);
  }
  return null;
}

function getCpuCoolerHeightMm(part: { name: string; specs?: Record<string, unknown> | null }): number | null {
  const keys = ["height_mm", "cooler_height_mm", "product_height_mm", "height"];
  for (const key of keys) {
    const parsed = parseNumberLike(part.specs?.[key]);
    if (parsed && parsed > 0) {
      return parsed;
    }
  }
  const match = part.name.match(/(\d{2,3})\s*mm/i);
  if (match) {
    return Number(match[1]);
  }
  return null;
}

function getPsuWattage(part: { name: string; specs?: Record<string, unknown> | null }): number | null {
  const fromSpecs = parseNumberLike(part.specs?.wattage ?? part.specs?.power_w);
  if (fromSpecs && fromSpecs > 0) {
    return fromSpecs;
  }
  const match = part.name.match(/(\d{3,4})\s*w/i);
  if (match) {
    return Number(match[1]);
  }
  return null;
}

function getCaseSupportedMotherboardFormFactors(part: { specs?: Record<string, unknown> | null }): string[] {
  const keys = ["supported_mb_form_factors", "supported_form_factors", "mb_form_factors"];
  for (const key of keys) {
    const value = part.specs?.[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => normalizeFormFactorToken(String(item)))
        .filter(Boolean);
    }
    if (typeof value === "string" && value.trim()) {
      return value
        .split(/[,/|\s]+/)
        .map((item) => normalizeFormFactorToken(item))
        .filter(Boolean);
    }
  }
  return [];
}

function isCaseCompatibleWithMotherboard(casePart: { name: string; specs?: Record<string, unknown> | null }, motherboardPart: { name: string; specs?: Record<string, unknown> | null } | null): boolean {
  if (!motherboardPart) {
    return true;
  }
  const mbFormFactor = getPartFormFactor(motherboardPart);
  if (!mbFormFactor) {
    return true;
  }
  const explicitSupported = getCaseSupportedMotherboardFormFactors(casePart);
  if (explicitSupported.length > 0) {
    return explicitSupported.includes(mbFormFactor);
  }
  const rank: Record<string, number> = {
    "mini-itx": 1,
    "micro-atx": 2,
    atx: 3,
    eatx: 4,
  };
  const caseRank = rank[getPartFormFactor(casePart)];
  const mbRank = rank[mbFormFactor];
  if (!caseRank || !mbRank) {
    return true;
  }
  return caseRank >= mbRank;
}

function checkPartCompatibility(
  category: string,
  candidate: SavedPartResponse,
  selectedParts: NormalizedResultPart[],
  requiredPsuWatt: number
): CompatibilityCheckResult {
  const reasons: string[] = [];
  const candidateSocket = normalizeSpecText(candidate.specs?.socket);
  const candidateMemoryType = normalizeSpecText(candidate.specs?.memory_type);

  const currentCpu = selectedParts.find((part) => part.category === "cpu" && !part.isPlaceholder) ?? null;
  const currentMotherboard = selectedParts.find((part) => part.category === "motherboard" && !part.isPlaceholder) ?? null;
  const currentMemory = selectedParts.find((part) => part.category === "memory" && !part.isPlaceholder) ?? null;
  const currentCase = selectedParts.find((part) => part.category === "case" && !part.isPlaceholder) ?? null;
  const currentCpuCooler = selectedParts.find((part) => part.category === "cpu_cooler" && !part.isPlaceholder) ?? null;

  const currentCpuSocket = normalizeSpecText(currentCpu?.specs?.socket);
  const currentCpuMemoryType = normalizeSpecText(currentCpu?.specs?.memory_type);
  const currentMbSocket = normalizeSpecText(currentMotherboard?.specs?.socket);
  const currentMbMemoryType = normalizeSpecText(currentMotherboard?.specs?.memory_type);
  const currentMemoryType = normalizeSpecText(currentMemory?.specs?.memory_type);

  if (category === "cpu") {
    if (candidateSocket && currentMbSocket && candidateSocket !== currentMbSocket) {
      reasons.push("CPUソケットが現在のマザーボードと一致しません。");
    }
    if (candidateMemoryType && currentMbMemoryType && candidateMemoryType !== currentMbMemoryType) {
      reasons.push("CPUの対応メモリ規格が現在のマザーボードと一致しません。");
    }
    if (candidateMemoryType && currentMemoryType && candidateMemoryType !== currentMemoryType) {
      reasons.push("CPUの対応メモリ規格が現在のメモリと一致しません。");
    }
  }

  if (category === "motherboard") {
    if (candidateSocket && currentCpuSocket && candidateSocket !== currentCpuSocket) {
      reasons.push("マザーボードのソケットが現在のCPUと一致しません。");
    }
    if (candidateMemoryType && currentCpuMemoryType && candidateMemoryType !== currentCpuMemoryType) {
      reasons.push("マザーボードのメモリ規格が現在のCPUと一致しません。");
    }
    if (candidateMemoryType && currentMemoryType && candidateMemoryType !== currentMemoryType) {
      reasons.push("マザーボードのメモリ規格が現在のメモリと一致しません。");
    }
  }

  if (category === "memory") {
    if (candidateMemoryType && currentMbMemoryType && candidateMemoryType !== currentMbMemoryType) {
      reasons.push("メモリ規格が現在のマザーボードと一致しません。");
    }
    if (candidateMemoryType && currentCpuMemoryType && candidateMemoryType !== currentCpuMemoryType) {
      reasons.push("メモリ規格が現在のCPU対応規格と一致しません。");
    }
  }

  if (category === "case") {
    if (!isCaseCompatibleWithMotherboard({ name: candidate.name, specs: candidate.specs }, currentMotherboard)) {
      reasons.push("ケースサイズが現在のマザーボードフォームファクタに対応していません。");
    }
    if (currentCpuCooler) {
      const caseMaxCooler = getCaseMaxCoolerHeightMm({ name: candidate.name, specs: candidate.specs });
      const coolerHeight = getCpuCoolerHeightMm(currentCpuCooler);
      if (caseMaxCooler && coolerHeight && coolerHeight > caseMaxCooler) {
        reasons.push("ケースのCPUクーラー高制限を超えるため、現在のCPUクーラーが収まりません。");
      }
    }
  }

  if (category === "cpu_cooler") {
    if (currentCase) {
      const caseMaxCooler = getCaseMaxCoolerHeightMm(currentCase);
      const coolerHeight = getCpuCoolerHeightMm({ name: candidate.name, specs: candidate.specs });
      if (caseMaxCooler && coolerHeight && coolerHeight > caseMaxCooler) {
        reasons.push("CPUクーラー高が現在のケース許容値を超えています。");
      }
    }
  }

  if (category === "psu") {
    const candidateWattage = getPsuWattage({ name: candidate.name, specs: candidate.specs });
    if (candidateWattage && candidateWattage < requiredPsuWatt) {
      reasons.push(`PSU容量が不足しています（推奨 ${requiredPsuWatt}W 以上）。`);
    }
  }

  return {
    ok: reasons.length === 0,
    reasons,
  };
}

export function ResultView({ config, onBack, onSavedConfiguration }: ResultProps) {
  const formatCurrency = (price: number) =>
    new Intl.NumberFormat("ja-JP", {
      style: "currency",
      currency: "JPY",
    }).format(price);

  const PART_CATEGORY_LABELS: Record<string, string> = {
    cpu: "CPU",
    cpu_cooler: "CPUクーラー",
    gpu: "グラフィックボード",
    motherboard: "マザーボード",
    memory: "メモリー",
    storage: "ストレージ",
    storage2: "ストレージ2",
    storage3: "ストレージ3",
    os: "OS",
    psu: "電源",
    case: "ケース",
  };

  const isSavedConfiguration = (value: GenerateConfigResponse | SavedConfigurationResponse): value is SavedConfigurationResponse =>
    "created_at" in value;

  const sortPartsByDisplayOrder = (parts: NormalizedResultPart[]) => {
    return [...parts].sort((left, right) => {
      const leftIndex = PART_DISPLAY_ORDER.indexOf(left.category as (typeof PART_DISPLAY_ORDER)[number]);
      const rightIndex = PART_DISPLAY_ORDER.indexOf(right.category as (typeof PART_DISPLAY_ORDER)[number]);
      const normalizedLeft = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
      const normalizedRight = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;
      return normalizedLeft - normalizedRight;
    });
  };

  const IGPU_USAGES = new Set(["general", "business", "standard"]);

  const normalizedParts: NormalizedResultPart[] = isSavedConfiguration(config)
    ? (() => {
      const parts: NormalizedResultPart[] = [
            ["cpu", config.cpu_data],
            ["cpu_cooler", config.cpu_cooler_data],
            ["gpu", config.gpu_data],
            ["motherboard", config.motherboard_data],
            ["memory", config.memory_data],
            ["storage", config.storage_data],
            ["storage2", config.storage2_data],
            ["storage3", config.storage3_data],
            ["os", config.os_data],
            ["psu", config.psu_data],
            ["case", config.case_data],
          ]
            .filter((entry): entry is [string, SavedPartResponse] => entry[1] !== null)
            .map(([category, part]) => ({
              category,
              partType: part.part_type,
              partId: part.id,
              name: part.name,
              price: part.price,
              url: part.url,
              specs: part.specs,
            }));
        // iGPU 構成では gpu_data が null になるため、保存済み構成でも内蔵GPU行を復元する。
        if (IGPU_USAGES.has(config.usage) && config.gpu_data === null) {
          const cpuIndexForIgpu = parts.findIndex((p) => p.category === "cpu");
          parts.splice(cpuIndexForIgpu + 1, 0, {
            category: "gpu",
            partType: "gpu",
            partId: null,
            name: "内蔵GPU（統合グラフィックス）",
            price: 0,
            url: "",
            specs: null,
          });
        }
        return sortPartsByDisplayOrder(parts);
      })()
    : sortPartsByDisplayOrder(
        config.parts.map((part) => ({
          ...part,
          partType: part.category,
          partId: null,
          specs: part.specs ?? null,
        }))
      );

  const displayParts = useMemo(() => {
    const parts = [...normalizedParts];
    for (const category of ["storage2", "storage3"]) {
      if (!parts.some((part) => part.category === category)) {
        parts.push({
          category,
          partType: category,
          partId: null,
          name: "未選択",
          price: 0,
          url: "",
          specs: null,
          isPlaceholder: true,
        });
      }
    }
    // 注意: この cpu_cooler は表示統一用のダミーであり、実在する保存部品データではない。
    // API の値が null でも、UI上は同じセクション位置に説明を出せるようにしている。
    if (!parts.some((part) => part.category === "cpu_cooler")) {
      parts.push({
        category: "cpu_cooler",
        partType: "cpu_cooler",
        partId: null,
        name: "未選択",
        price: 0,
        url: "",
        specs: null,
        isPlaceholder: true,
      });
    }
    return sortPartsByDisplayOrder(parts);
  }, [normalizedParts]);

  const [editedParts, setEditedParts] = useState<NormalizedResultPart[] | null>(null);
  const [openEditorCategory, setOpenEditorCategory] = useState<string | null>(null);
  const [partCandidatesByCategory, setPartCandidatesByCategory] = useState<Record<string, SavedPartResponse[]>>({});
  const [partCandidatesLoading, setPartCandidatesLoading] = useState(false);
  const [partCandidatesError, setPartCandidatesError] = useState<string | null>(null);
  const [candidateQuery, setCandidateQuery] = useState("");
  const [candidateMaker, setCandidateMaker] = useState("all");
  const [candidateMinPrice, setCandidateMinPrice] = useState("");
  const [candidateMaxPrice, setCandidateMaxPrice] = useState("");
  const [ignorePriceRange, setIgnorePriceRange] = useState(true);
  const [showAllCandidates, setShowAllCandidates] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccessMessage, setSaveSuccessMessage] = useState<string | null>(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [editingConfigurationName, setEditingConfigurationName] = useState<string>("");
  const [nameUpdateLoading, setNameUpdateLoading] = useState(false);
  const [nameUpdateError, setNameUpdateError] = useState<string | null>(null);
  const [pendingIncompatibleSelection, setPendingIncompatibleSelection] = useState<PendingIncompatibleSelection | null>(null);
  const [baseSavedConfig, setBaseSavedConfig] = useState<SavedConfigurationResponse | null>(
    isSavedConfiguration(config) ? config : null
  );

  useEffect(() => {
    setEditedParts(null);
    setOpenEditorCategory(null);
    setPartCandidatesByCategory({});
    setPartCandidatesError(null);
    setPartCandidatesLoading(false);
    setCandidateQuery("");
    setCandidateMaker("all");
    setCandidateMinPrice("");
    setCandidateMaxPrice("");
    setIgnorePriceRange(true);
    setShowAllCandidates(false);
    setSaveError(null);
    setSaveSuccessMessage(null);
    setSaveLoading(false);
    setEditingConfigurationName(isSavedConfiguration(config) ? (config.name || "") : "");
    setNameUpdateError(null);
    setNameUpdateLoading(false);
    setPendingIncompatibleSelection(null);
    setBaseSavedConfig(isSavedConfiguration(config) ? config : null);
  }, [config]);

  useEffect(() => {
    if (isSavedConfiguration(config)) {
      return;
    }
    if (!config.configuration_id) {
      return;
    }
    let cancelled = false;
    const loadBase = async () => {
      try {
        const saved = await getSavedConfigurationById(config.configuration_id as number);
        if (!cancelled) {
          setBaseSavedConfig(saved);
        }
      } catch {
        if (!cancelled) {
          setBaseSavedConfig(null);
        }
      }
    };
    void loadBase();
    return () => {
      cancelled = true;
    };
  }, [config]);

  const activeDisplayParts = editedParts ?? displayParts;
  const hasManualEdits = editedParts !== null;

  const resolveCandidatePartType = (category: string) => {
    if (category === "storage2" || category === "storage3") {
      return "storage";
    }
    return category;
  };

  const mapUsageForSave = (usage: string): "gaming" | "video_editing" | "general" => {
    if (usage === "gaming") {
      return "gaming";
    }
    if (usage === "creator" || usage === "video_editing") {
      return "video_editing";
    }
    return "general";
  };

  const buildPartIdMap = (parts: NormalizedResultPart[]) => {
    const partMap: Record<string, number | null> = {
      cpu: null,
      cpu_cooler: null,
      gpu: null,
      motherboard: null,
      memory: null,
      storage: null,
      storage2: null,
      storage3: null,
      os: null,
      psu: null,
      case: null,
    };

    if (baseSavedConfig) {
      partMap.cpu = baseSavedConfig.cpu_data?.id ?? null;
      partMap.cpu_cooler = baseSavedConfig.cpu_cooler_data?.id ?? null;
      partMap.gpu = baseSavedConfig.gpu_data?.id ?? null;
      partMap.motherboard = baseSavedConfig.motherboard_data?.id ?? null;
      partMap.memory = baseSavedConfig.memory_data?.id ?? null;
      partMap.storage = baseSavedConfig.storage_data?.id ?? null;
      partMap.storage2 = baseSavedConfig.storage2_data?.id ?? null;
      partMap.storage3 = baseSavedConfig.storage3_data?.id ?? null;
      partMap.os = baseSavedConfig.os_data?.id ?? null;
      partMap.psu = baseSavedConfig.psu_data?.id ?? null;
      partMap.case = baseSavedConfig.case_data?.id ?? null;
    }

    for (const part of parts) {
      if (!Object.prototype.hasOwnProperty.call(partMap, part.category)) {
        continue;
      }
      if (part.isPlaceholder || part.name.includes("内蔵GPU")) {
        partMap[part.category] = null;
        continue;
      }
      if (typeof part.partId === "number") {
        partMap[part.category] = part.partId;
      }
    }

    return partMap;
  };

  const openPartEditor = async (category: string) => {
    if (!EDITABLE_PART_CATEGORIES.has(category as (typeof PART_DISPLAY_ORDER)[number])) {
      return;
    }

    setOpenEditorCategory(category);
    setCandidateQuery("");
    setCandidateMaker("all");
    setCandidateMinPrice("");
    setCandidateMaxPrice("");
    setShowAllCandidates(false);
    setPartCandidatesError(null);
    const candidateType = resolveCandidatePartType(category);

    if (partCandidatesByCategory[candidateType]) {
      return;
    }

    setPartCandidatesLoading(true);
    try {
      const candidates = await getPartsByType(candidateType, { slotCategory: category });
      const sorted = candidates
        .slice()
        .sort((left, right) => {
          if (left.price !== right.price) {
            return left.price - right.price;
          }
          return left.name.localeCompare(right.name, "ja");
        });
      setPartCandidatesByCategory((previous) => ({
        ...previous,
        [candidateType]: sorted,
      }));
    } catch (error) {
      setPartCandidatesError(error instanceof Error ? error.message : "候補パーツの取得に失敗しました。");
    } finally {
      setPartCandidatesLoading(false);
    }
  };

  const applyManualPartSelection = (category: string, selected: SavedPartResponse) => {
    setEditedParts((previous) => {
      const baseParts = previous ?? displayParts;
      const nextPart: NormalizedResultPart = {
        category,
        partType: selected.part_type,
        partId: selected.id,
        name: selected.name,
        price: selected.price,
        url: selected.url,
        specs: selected.specs ?? null,
      };
      const existingIndex = baseParts.findIndex((part) => part.category === category);
      if (existingIndex === -1) {
        return sortPartsByDisplayOrder([...baseParts, nextPart]);
      }
      const cloned = [...baseParts];
      cloned[existingIndex] = nextPart;
      return sortPartsByDisplayOrder(cloned);
    });
    setOpenEditorCategory(null);
  };

  const unsetOptionalPart = (category: "storage2" | "storage3" | "cpu_cooler") => {
    setEditedParts((previous) => {
      const baseParts = previous ?? displayParts;
      const placeholder: NormalizedResultPart = {
        category,
        partType: category,
        partId: null,
        name: "未選択",
        price: 0,
        url: "",
        specs: null,
        isPlaceholder: true,
      };
      const existingIndex = baseParts.findIndex((part) => part.category === category);
      if (existingIndex === -1) {
        return sortPartsByDisplayOrder([...baseParts, placeholder]);
      }
      const cloned = [...baseParts];
      cloned[existingIndex] = placeholder;
      return sortPartsByDisplayOrder(cloned);
    });
    setOpenEditorCategory(null);
  };

  const handleSaveEditedConfiguration = async () => {
    setSaveError(null);
    setSaveSuccessMessage(null);
    setSaveLoading(true);
    try {
      const sourceParts = editedParts ?? activeDisplayParts;
      const partMap = buildPartIdMap(sourceParts);
      const requiredKeys: Array<keyof typeof partMap> = ["cpu", "motherboard", "memory", "storage", "os", "psu", "case"];
      const missingRequired = requiredKeys.filter((key) => !partMap[key]);
      if (missingRequired.length > 0) {
        throw new Error("保存に必要な主要パーツIDが不足しています。主要パーツを候補から再選択してください。");
      }

      const saved = await createSavedConfiguration({
        name: "name" in config ? config.name : undefined,
        budget: requestedBudget,
        usage: mapUsageForSave(config.usage),
        cpu: partMap.cpu,
        cpu_cooler: partMap.cpu_cooler,
        gpu: partMap.gpu,
        motherboard: partMap.motherboard,
        memory: partMap.memory,
        storage: partMap.storage,
        storage2: partMap.storage2,
        storage3: partMap.storage3,
        os: partMap.os,
        psu: partMap.psu,
        case: partMap.case,
      });

      setSaveSuccessMessage(`保存しました: ID ${saved.id}`);
      if (onSavedConfiguration) {
        onSavedConfiguration(saved);
      }
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "編集後構成の保存に失敗しました。");
    } finally {
      setSaveLoading(false);
    }
  };

  const handleExportPdf = () => {
    const CATEGORY_LABELS: Record<string, string> = {
      cpu: "CPU",
      cpu_cooler: "CPUクーラー",
      gpu: "GPU",
      motherboard: "マザーボード",
      memory: "メモリ",
      storage: "ストレージ",
      storage2: "ストレージ 2",
      storage3: "ストレージ 3",
      os: "OS",
      psu: "電源",
      case: "ケース",
    };

    const partsForPdf = activeDisplayParts.filter((p) => !p.isPlaceholder && p.price > 0);
    const totalPrice = partsForPdf.reduce((sum, p) => sum + p.price, 0);
    const issueDate = new Date().toLocaleDateString("ja-JP", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });

    const partsRows = partsForPdf
      .map(
        (part) => `
        <tr>
          <td>${CATEGORY_LABELS[part.category] ?? part.category}</td>
          <td>${part.name.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</td>
          <td class="price">¥${part.price.toLocaleString("ja-JP")}</td>
        </tr>`,
      )
      .join("");

    const html = `<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <title>PC構成見積書</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Yu Gothic UI", "Hiragino Sans", "Noto Sans JP", "Segoe UI", sans-serif;
      font-size: 12pt;
      color: #111;
      background: #fff;
      padding: 32px 40px;
    }
    h1 { font-size: 22pt; margin-bottom: 4px; }
    .meta { font-size: 10pt; color: #555; margin-bottom: 24px; }
    .summary {
      display: flex;
      gap: 32px;
      margin-bottom: 24px;
      padding: 12px 16px;
      border: 1px solid #ccc;
      border-radius: 6px;
      background: #f8f9fa;
    }
    .summary-item { display: flex; flex-direction: column; gap: 2px; }
    .summary-item .label { font-size: 9pt; color: #666; }
    .summary-item .value { font-size: 13pt; font-weight: bold; }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 24px;
    }
    thead tr { background: #1e3a5f; color: #fff; }
    thead th {
      padding: 8px 10px;
      text-align: left;
      font-size: 10pt;
    }
    tbody tr:nth-child(even) { background: #f4f6fb; }
    tbody td {
      padding: 7px 10px;
      border-bottom: 1px solid #e0e0e0;
      font-size: 10pt;
      vertical-align: top;
    }
    td.price { text-align: right; white-space: nowrap; font-weight: 600; }
    tfoot td {
      padding: 10px 10px;
      font-weight: bold;
      font-size: 12pt;
      border-top: 2px solid #1e3a5f;
    }
    tfoot td.price { font-size: 14pt; color: #1e3a5f; }
    .power { margin-top: 16px; font-size: 10pt; color: #444; }
    .footer { margin-top: 40px; font-size: 9pt; color: #888; border-top: 1px solid #ddd; padding-top: 8px; }
    @media print {
      body { padding: 16px 20px; }
    }
  </style>
</head>
<body>
  <h1>PC構成見積書</h1>
  <p class="meta">発行日: ${issueDate}</p>

  <div class="summary">
    <div class="summary-item">
      <span class="label">用途</span>
      <span class="value">${usageLabel}</span>
    </div>
    <div class="summary-item">
      <span class="label">指定予算</span>
      <span class="value">¥${requestedBudget.toLocaleString("ja-JP")}</span>
    </div>
    <div class="summary-item">
      <span class="label">合計金額</span>
      <span class="value" style="color:#0d6e30;">¥${totalPrice.toLocaleString("ja-JP")}</span>
    </div>
    <div class="summary-item">
      <span class="label">推定消費電力</span>
      <span class="value">${estimatedPower}W</span>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th style="width:13%">カテゴリ</th>
        <th>パーツ名</th>
        <th style="width:14%">価格（税込）</th>
      </tr>
    </thead>
    <tbody>
      ${partsRows}
    </tbody>
    <tfoot>
      <tr>
        <td colspan="2">合計</td>
        <td class="price">¥${totalPrice.toLocaleString("ja-JP")}</td>
      </tr>
    </tfoot>
  </table>

  <p class="power">推定消費電力: ${estimatedPower}W（実際の消費電力は使用環境により異なります）</p>

  <div class="footer">
    ※ 価格は生成時点の参考価格です。最新価格は各購入ページでご確認ください。<br />
    ※ 本見積書はポートフォリオ用デモアプリが自動生成したものです。
  </div>
</body>
</html>`;

    const printWindow = window.open("", "_blank", "width=900,height=700");
    if (!printWindow) {
      alert("ポップアップがブロックされました。ブラウザのポップアップ許可を有効にしてください。");
      return;
    }
    printWindow.document.write(html);
    printWindow.document.close();
    printWindow.focus();
    // ブラウザがスタイルを適用後に印刷ダイアログを開く
    printWindow.onload = () => {
      printWindow.print();
    };
  };

  const displayedTotalPrice = hasManualEdits
    ? activeDisplayParts.reduce((sum, part) => {
        if (part.isPlaceholder) {
          return sum;
        }
        return sum + part.price;
      }, 0)
    : config.total_price;

  const inferStorageCapacityGb = (part: { name: string; specs?: Record<string, unknown> | null }) => {
    const capacity = Number(part.specs?.capacity_gb ?? 0);
    if (capacity > 0) {
      return capacity;
    }
    // 容量表記は TB を優先し、モデル番号に埋まった GB 表記は除外する。
    const tbMatch = part.name.match(/(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*TB/i);
    if (tbMatch) {
      return Math.round(Number(tbMatch[1]) * 1024);
    }
    const gbMatch = part.name.match(/(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*GB/i);
    if (gbMatch) {
      return Math.round(Number(gbMatch[1]));
    }
    return 0;
  };

  const inferStorageInterface = (part: { name: string; specs?: Record<string, unknown> | null }) => {
    const interfaceValue = String(part.specs?.interface ?? "").toLowerCase();
    if (interfaceValue === "nvme") {
      return "nvme";
    }
    if (interfaceValue === "sata") {
      return "sata";
    }
    const name = part.name.toLowerCase();
    if (name.includes("nvme")) {
      return "nvme";
    }
    if (name.includes("sata")) {
      return "sata";
    }
    // Western Digital の NVMe 系モデル番号 (SN500/580/700/750/850) を判定する。
    if (/\bsn[5-9]\d{2}\b/.test(name)) {
      return "nvme";
    }
    // Western Digital の SATA SSD 系モデル番号 (SA500) を判定する。
    if (/\bsa\d{3}\b/.test(name)) {
      return "sata";
    }
    // Samsung の NVMe 系モデル番号 (970 / 980 / 990 EVO・PRO) を判定する。
    if (/\b(970|980|990)\s*(evo|pro)\b/i.test(name)) {
      return "nvme";
    }
    // 名前に M.2 が含まれる場合は NVMe とみなす。
    if (name.includes("m.2")) {
      return "nvme";
    }
    return "other";
  };

  const inferStorageMediaTypeFromPart = (part: { name: string; specs?: Record<string, unknown> | null }) => {
    const text = part.name.toLowerCase();
    const formFactor = String(part.specs?.form_factor ?? "").toLowerCase();
    const interfaceValue = inferStorageInterface(part);

    if (interfaceValue === "nvme") {
      return "ssd" as const;
    }
    if (text.includes("ssd") || formFactor.includes("m.2") || formFactor.includes("2.5inch") || text.includes("m.2")) {
      return "ssd" as const;
    }
    // Western Digital の SSD 系モデル番号を SSD として判定する。
    if (/\b(sa500|sn500|sn580|sn700|sn750|sn850)\b/.test(text)) {
      return "ssd" as const;
    }
    if (/(5400|7200|10000|15000)\s*rpm/i.test(part.name)) {
      return "hdd" as const;
    }
    // HDD 系キーワードを判定する。wd red 単体は SSD 系と紛らわしいため除外する。
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
      return "hdd" as const;
    }
    if (interfaceValue === "sata" && formFactor.includes("3.5")) {
      return "hdd" as const;
    }
    if (interfaceValue === "sata" && (formFactor.includes("2.5") || formFactor.includes("m.2"))) {
      return "ssd" as const;
    }
    return "other" as const;
  };

  const formatCapacityLabel = (capacityGb: number) => {
    if (capacityGb <= 0) {
      return null;
    }
    if (capacityGb >= 1024) {
      const tb = capacityGb / 1024;
      return Number.isInteger(tb) ? `${tb}TB` : `${tb.toFixed(1)}TB`;
    }
    return `${capacityGb}GB`;
  };

  const getStoragePartMeta = (part: NormalizedResultPart) => {
    const mediaType = inferStorageMediaTypeFromPart(part);
    const interfaceType = inferStorageInterface(part);
    const capacityLabel = formatCapacityLabel(inferStorageCapacityGb(part));
    const formFactor = String(part.specs?.form_factor ?? "").trim();

    return {
      mediaLabel: STORAGE_MEDIA_LABELS[mediaType],
      interfaceLabel: STORAGE_INTERFACE_LABELS[interfaceType],
      capacityLabel,
      formFactor: formFactor || null,
    };
  };

  const inferMemoryCapacityGb = (part: NormalizedResultPart) => {
    const specCapacity = Number(part.specs?.capacity_gb ?? 0);
    if (specCapacity > 0) {
      return specCapacity;
    }

    const text = part.name;
    const multiMatch = text.match(/(\d+)\s*GB\s*[x×*]\s*(\d+)/i) || text.match(/(\d+)\s*GB\s*(\d+)\s*枚組/i);
    if (multiMatch) {
      return Number(multiMatch[1]) * Number(multiMatch[2]);
    }

    const singleMatch = text.match(/(\d+)\s*GB/i);
    if (singleMatch) {
      return Number(singleMatch[1]);
    }
    return 0;
  };

  const inferMemoryModuleCount = (part: NormalizedResultPart) => {
    const specModule = Number(part.specs?.module_count ?? 0);
    if (specModule > 0) {
      return specModule;
    }
    const text = part.name;
    const multiMatch = text.match(/[x×*]\s*(\d+)/i) || text.match(/(\d+)\s*枚組/i);
    if (multiMatch) {
      return Number(multiMatch[1]);
    }
    return 1;
  };

  const inferCpuPower = (part: NormalizedResultPart | null) => {
    if (!part) {
      return 0;
    }
    const specTdp = Number(part.specs?.tdp_w ?? 0);
    if (specTdp > 0) {
      return specTdp;
    }
    const text = part.name.toLowerCase();
    for (const watts of [170, 125, 105, 95, 65, 35]) {
      if (text.includes(`${watts}w`)) {
        return watts;
      }
    }
    return 95;
  };

  const inferGpuPower = (part: NormalizedResultPart | null) => {
    if (!part) {
      return 0;
    }
    const specTdp = Number(part.specs?.tdp_w ?? 0);
    if (specTdp > 0) {
      return specTdp;
    }
    for (const [pattern, watts] of GPU_POWER_RULES) {
      if (pattern.test(part.name)) {
        return watts;
      }
    }
    return 180;
  };

  const estimatedPower = useMemo(() => {
    if (!hasManualEdits && !isSavedConfiguration(config)) {
      return config.estimated_power_w;
    }

    const cpu = activeDisplayParts.find((part) => part.category === "cpu" && !part.isPlaceholder) ?? null;
    const gpu = activeDisplayParts.find((part) => part.category === "gpu" && part.price > 0 && !part.isPlaceholder) ?? null;
    const cpuCooler = activeDisplayParts.find((part) => part.category === "cpu_cooler" && !part.isPlaceholder) ?? null;
    const motherboard = activeDisplayParts.find((part) => part.category === "motherboard" && !part.isPlaceholder) ?? null;
    const memory = activeDisplayParts.find((part) => part.category === "memory" && !part.isPlaceholder) ?? null;
    const storageParts = activeDisplayParts.filter((part) => ["storage", "storage2", "storage3"].includes(part.category) && !part.isPlaceholder);
    const hasCase = activeDisplayParts.some((part) => part.category === "case" && !part.isPlaceholder);

    const cpuPower = inferCpuPower(cpu);
    const gpuPower = inferGpuPower(gpu);
    const motherboardPower = motherboard ? 45 : 0;
    const memoryPower = memory ? 10 : 0;
    const storagePower = storageParts.reduce((sum, part) => sum + (inferStorageMediaTypeFromPart(part) === "hdd" ? 12 : 6), 0);
    const coolerText = `${cpuCooler?.name ?? ""}`.toLowerCase();
    const coolerPower = cpuCooler ? ((coolerText.includes("水冷") || coolerText.includes("aio") || coolerText.includes("360") || coolerText.includes("280") || coolerText.includes("240")) ? 20 : 8) : 0;
    const casePower = hasCase ? 10 : 0;

    return cpuPower + gpuPower + motherboardPower + memoryPower + storagePower + coolerPower + casePower;
  }, [activeDisplayParts, config, hasManualEdits]);

  const configurationId = isSavedConfiguration(config)
    ? config.id
    : config.configuration_id;

  const USAGE_LABELS: Record<string, string> = {
    gaming: "ゲーミングPC",
    creator: "クリエイターPC",
    ai: "AI PC（ローカルAI）",
    general: "汎用PC（事務・学習向け）",
  };
  const usageCode = normalizeUsageCode(config.usage);
  const usageLabel = USAGE_LABELS[usageCode] ?? config.usage;
  const marketBudgetAdjusted = !isSavedConfiguration(config) && Boolean(config.market_budget_adjusted);
  const marketBudgetNote = !isSavedConfiguration(config) ? (config.market_budget_note ?? "") : "";
  const requestedBudget = !isSavedConfiguration(config)
    ? (config.requested_budget ?? config.budget)
    : config.budget;
  const adjustedBudget = !isSavedConfiguration(config) ? config.budget : config.budget;
  const hasBudgetCorrection = marketBudgetAdjusted && adjustedBudget !== requestedBudget;
  const isBudgetRaised = hasBudgetCorrection && adjustedBudget > requestedBudget;
  const budgetCorrectionLabel = isBudgetRaised ? "引き上げ" : "引き下げ";
  const budgetCorrectionStyle = isBudgetRaised
    ? "border-emerald-300 bg-emerald-50 text-emerald-900"
    : "border-rose-300 bg-rose-50 text-rose-900";
  const buildPriorityCode: "cost" | "spec" | "balanced" = !isSavedConfiguration(config)
    ? (config.build_priority ?? "balanced")
    : "balanced";
  const buildPriorityLabel = !isSavedConfiguration(config)
    ? (
        buildPriorityCode === "cost"
          ? "コスト重視"
          : buildPriorityCode === "spec"
            ? "スペック重視"
            : "バランス"
      )
    : "不明（保存履歴）";
  const budgetTierLabel = config.budget_tier_label ?? "不明";
  const benchmarkFloorScore = !isSavedConfiguration(config)
    ? Number(config.minimum_gaming_gpu_perf_score ?? 0)
    : 0;
  const selectedGpuBenchmarkScore = !isSavedConfiguration(config)
    ? Number(config.selected_gpu_perf_score ?? 0)
    : 0;
  const selectedGpuGamingTierLabel = !isSavedConfiguration(config)
    ? config.selected_gpu_gaming_tier_label ?? ""
    : "";
  const partAdjustments = !isSavedConfiguration(config)
    ? (config.part_adjustments ?? [])
    : [];
  const configAutoAdjusted = !isSavedConfiguration(config) && partAdjustments.length > 0;
  const showConfigAdjustmentNotice =
    configAutoAdjusted
    || (!isSavedConfiguration(config) && typeof config.recommended_budget_min_for_x3d === "number");

  const currentGpuPart = activeDisplayParts.find((part) => part.category === "gpu" && part.price > 0)
    ?? activeDisplayParts.find((part) => part.category === "gpu")
    ?? null;
  const currentGpuModelKey = currentGpuPart ? extractGpuModelKey(currentGpuPart.name) : null;
  const currentGpuModelKeyNormalized = currentGpuModelKey ? normalizeGpuModelKey(currentGpuModelKey) : null;
  const currentCpuPart = activeDisplayParts.find((part) => part.category === "cpu") ?? null;
  const currentCpuModelKey = currentCpuPart ? extractCpuModelKey(currentCpuPart.name) : null;
  const currentCpuModelKeyNormalized = currentCpuModelKey ? normalizeCpuModelKey(currentCpuModelKey) : null;
  const isGamingUsage = usageCode === "gaming";
  const creatorCpuRecommendationText = usageCode === "creator"
    ? "ゲーム配信をするならRyzen 9 9950X3Dがおすすめです。"
    : "";
  // 注意: CPUランキング表示モード切替は gaming 専用仕様。
  // creator/ai/general に同ロジックを流用する場合は比較軸の再定義が必要。
  const gamingCpuRankingMode = isGamingUsage && !isSavedConfiguration(config) && config.build_priority === "cost" ? "cost" : "spec";

  const [gpuComparison, setGpuComparison] = useState<GpuPerformanceCompareResponse | null>(null);
  const [gpuSnapshot, setGpuSnapshot] = useState<GpuPerformanceLatestResponse["snapshot"] | null>(null);
  const [gpuComparisonLoading, setGpuComparisonLoading] = useState(false);
  const [gpuComparisonError, setGpuComparisonError] = useState<string | null>(null);
  const [cpuComparison, setCpuComparison] = useState<CpuSelectionMaterialCompareResponse | null>(null);
  const [cpuMaterialMeta, setCpuMaterialMeta] = useState<Pick<CpuSelectionMaterialLatestResponse, "entry_count" | "excluded_count" | "exclude_intel_13_14"> | null>(null);
  const [cpuComparisonLoading, setCpuComparisonLoading] = useState(false);
  const [cpuComparisonError, setCpuComparisonError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!currentGpuModelKey) {
      setGpuComparison(null);
      setGpuSnapshot(null);
      setGpuComparisonError(null);
      setGpuComparisonLoading(false);
      return () => {
        cancelled = true;
      };
    }

    const loadGpuComparison = async () => {
      setGpuComparisonLoading(true);
      setGpuComparisonError(null);

      try {
        const latest = await getLatestGpuPerformance();
        const currentEntry = latest.entries.results.find(
          (entry) => normalizeGpuModelKey(entry.model_key) === currentGpuModelKeyNormalized,
        );

        if (!currentEntry) {
          if (!cancelled) {
            setGpuComparison(null);
            setGpuSnapshot(latest.snapshot);
            setGpuComparisonError(`GPU性能データに ${currentGpuModelKey} が見つかりませんでした。`);
          }
          return;
        }

        const nearbyModelKeys = latest.entries.results
          .filter((entry) => Math.abs(entry.rank_global - currentEntry.rank_global) <= 2)
          .sort((left, right) => left.rank_global - right.rank_global)
          .map((entry) => entry.model_key);

        const compare = await compareGpuPerformance(nearbyModelKeys.length > 0 ? nearbyModelKeys : [currentGpuModelKey]);

        if (!cancelled) {
          setGpuSnapshot(latest.snapshot);
          setGpuComparison(compare);
          setGpuComparisonError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setGpuComparison(null);
          setGpuSnapshot(null);
          setGpuComparisonError(error instanceof Error ? error.message : "GPU性能比較の取得に失敗しました。");
        }
      } finally {
        if (!cancelled) {
          setGpuComparisonLoading(false);
        }
      }
    };

    void loadGpuComparison();

    return () => {
      cancelled = true;
    };
  }, [currentGpuModelKey, currentGpuModelKeyNormalized]);

  useEffect(() => {
    let cancelled = false;

    if (!currentCpuModelKey && !isGamingUsage) {
      setCpuComparison(null);
      setCpuMaterialMeta(null);
      setCpuComparisonError(null);
      setCpuComparisonLoading(false);
      return () => {
        cancelled = true;
      };
    }

    const normalizeModel = (value: string) => value.replace(/\s+/g, " ").trim().toUpperCase();

    const loadCpuComparison = async () => {
      setCpuComparisonLoading(true);
      setCpuComparisonError(null);

      try {
        const latest = await getLatestCpuSelectionMaterial();

        if (isGamingUsage) {
          const gamingRanking = sortGamingCpuEntries(latest.entries.results, gamingCpuRankingMode);

          if (!cancelled) {
            setCpuMaterialMeta({
              entry_count: latest.entry_count,
              excluded_count: latest.excluded_count,
              exclude_intel_13_14: latest.exclude_intel_13_14,
            });

            if (gamingRanking.length === 0) {
              setCpuComparison(null);
              setCpuComparisonError("ゲーミングCPU順位のAMD候補が見つかりませんでした。");
              return;
            }

            setCpuComparison({
              requested_models: gamingRanking.map((entry) => entry.model_name),
              missing_models: [],
              results: gamingRanking,
            });
            setCpuComparisonError(null);
          }
          return;
        }

        if (!currentCpuModelKey) {
          return;
        }

        const sorted = latest.entries.results
          .slice()
          .sort((left, right) => right.perf_score - left.perf_score);

        const currentIndex = sorted.findIndex((entry) => {
          const model = normalizeModel(entry.model_name);
          return model === currentCpuModelKey || model.includes(currentCpuModelKey) || currentCpuModelKey.includes(model);
        });

        if (currentIndex < 0) {
          if (!cancelled) {
            setCpuComparison(null);
            setCpuMaterialMeta({
              entry_count: latest.entry_count,
              excluded_count: latest.excluded_count,
              exclude_intel_13_14: latest.exclude_intel_13_14,
            });
            setCpuComparisonError(`CPU選考資料に ${currentCpuModelKey} が見つかりませんでした。`);
          }
          return;
        }

        const start = Math.max(0, currentIndex - 2);
        const end = Math.min(sorted.length, currentIndex + 3);
        const nearbyModels = sorted.slice(start, end).map((entry) => entry.model_name);

        const compare = await compareCpuSelectionMaterial(nearbyModels.length > 0 ? nearbyModels : [currentCpuModelKey]);

        if (!cancelled) {
          setCpuMaterialMeta({
            entry_count: latest.entry_count,
            excluded_count: latest.excluded_count,
            exclude_intel_13_14: latest.exclude_intel_13_14,
          });
          setCpuComparison(compare);
          setCpuComparisonError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setCpuComparison(null);
          setCpuMaterialMeta(null);
          setCpuComparisonError(error instanceof Error ? error.message : "CPU選考資料の取得に失敗しました。");
        }
      } finally {
        if (!cancelled) {
          setCpuComparisonLoading(false);
        }
      }
    };

    void loadCpuComparison();

    return () => {
      cancelled = true;
    };
  }, [currentCpuModelKey, gamingCpuRankingMode, isGamingUsage]);

  const selectionSummary = {
    coolerType:
      !isSavedConfiguration(config) && config.cooler_type
        ? (config.cooler_type === "air" ? "空冷" : config.cooler_type === "liquid" ? "水冷" : "指定なし")
        : null,
    radiatorSize:
      !isSavedConfiguration(config) && config.radiator_size
        ? (config.radiator_size === "any" ? "指定なし" : `${config.radiator_size}mm`)
        : null,
    coolingProfile:
      !isSavedConfiguration(config) && config.cooling_profile
        ? (
            config.cooling_profile === "silent"
              ? "静音重視"
              : config.cooling_profile === "performance"
                ? "冷却重視"
                : "バランス"
          )
        : null,
    caseSize:
      !isSavedConfiguration(config) && config.case_size
        ? (
            config.case_size === "mini"
              ? "Mini"
              : config.case_size === "mid"
                ? "Mid"
                : config.case_size === "full"
                  ? "Full"
                  : "指定なし"
          )
        : null,
    caseFanPolicy:
      !isSavedConfiguration(config) && config.case_fan_policy
        ? (
            config.case_fan_policy === "silent"
              ? "静音重視"
              : config.case_fan_policy === "airflow"
                ? "冷却重視"
                : "自動"
          )
        : null,
    cpuVendor:
      !isSavedConfiguration(config) && config.cpu_vendor
        ? (
            config.cpu_vendor === "intel"
              ? "Intel"
              : config.cpu_vendor === "amd"
                ? "AMD"
                : "指定なし"
          )
        : null,
    buildPriority:
      !isSavedConfiguration(config) && config.build_priority
        ? (
            config.build_priority === "cost"
              ? "コスト重視"
              : config.build_priority === "spec"
                ? "スペック重視"
                : "バランス"
          )
        : null,
  };

  const hasCpuPart = activeDisplayParts.some((part) => part.category === "cpu" && !part.isPlaceholder);
  const hasDedicatedCpuCooler = activeDisplayParts.some((part) => part.category === "cpu_cooler" && !part.isPlaceholder);
  const showBundledCpuCoolerNote =
    !isSavedConfiguration(config)
    && hasCpuPart
    && !hasDedicatedCpuCooler;

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 p-6">
      <div className="max-w-4xl mx-auto">
        <div className="sticky top-4 z-30 mb-6 flex justify-start gap-3">
          <button
            onClick={onBack}
            className="rounded-lg bg-slate-600 px-4 py-2 font-semibold text-white shadow hover:bg-slate-700 transition"
          >
            ← 戻る
          </button>
          <button
            onClick={handleExportPdf}
            className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white shadow hover:bg-emerald-700 transition"
          >
            🖨 見積書PDF保存
          </button>
        </div>

        <div className="bg-white rounded-lg shadow-lg p-8 pb-28">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-3xl font-bold text-gray-800">
              構成提案が完成しました！
            </h2>
            {configAutoAdjusted && (
              <span className="inline-flex items-center rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">
                構成を自動調整しました
              </span>
            )}
            {marketBudgetAdjusted && (
              <span className="inline-flex items-center rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-800">
                相場補正
              </span>
            )}
          </div>
          <p className="text-gray-600 mb-6">
            用途: 
            <span className="font-semibold">
              {usageLabel}
            </span>
          </p>

          <div className="mb-6 flex flex-wrap items-center gap-2 text-sm">
            <span className="inline-flex items-center rounded-full bg-indigo-100 px-3 py-1 font-semibold text-indigo-800">
              予算帯: {budgetTierLabel}
            </span>
            <span className="inline-flex items-center rounded-full bg-cyan-100 px-3 py-1 font-semibold text-cyan-800">
              構成方針: {buildPriorityLabel}
            </span>
          </div>

          {hasManualEdits && (
            <div className="mb-6 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-900">
              <p className="font-semibold">手動で構成を変更中です。</p>
              <p className="mt-1 text-xs text-sky-800">各パーツ欄の「変更」から候補を選び直せます。合計金額と推定消費電力は画面内で再計算されます。</p>
            </div>
          )}

          {showConfigAdjustmentNotice && (
            <div className="mb-6 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <p className="font-semibold">構成パーツと選定条件を自動調整しました。</p>
              {!isSavedConfiguration(config) && typeof config.recommended_budget_min_for_x3d === "number" && (
                <p className="mt-1 text-xs text-amber-800">X3D必須構成の推奨下限: {formatCurrency(config.recommended_budget_min_for_x3d)}</p>
              )}
            </div>
          )}

          {marketBudgetAdjusted && (
            <div className="mb-6 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
              {marketBudgetNote ? (
                <p className="text-xs text-emerald-800">{marketBudgetNote}</p>
              ) : (
                <p className="font-semibold">相場変動により予算を補正しました。</p>
              )}
            </div>
          )}

          {!isSavedConfiguration(config) && config.message && (
            <div className="mb-6 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-900">
              <p className="font-semibold">選定ポリシーの自動調整</p>
              <p className="mt-1 text-xs text-sky-800">{config.message}</p>
            </div>
          )}

          {!isSavedConfiguration(config) && partAdjustments.length > 0 && (
            <div className="mb-6 rounded-lg border border-violet-300 bg-violet-50 px-4 py-3 text-sm text-violet-900">
              <p className="font-semibold">構成変更の内訳</p>
              <div className="mt-2 space-y-2">
                {partAdjustments.map((change, index) => (
                  <div key={`${change.category}-${index}`} className="rounded-md border border-violet-200 bg-white/70 px-3 py-2">
                    <p className="text-xs font-semibold text-violet-900">
                      {change.category_label ?? change.category}: {change.from_name} → {change.to_name}
                    </p>
                    <p className="mt-1 text-[11px] text-violet-800">
                      理由: {change.reason}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!isSavedConfiguration(config) && config.usage === "gaming" && benchmarkFloorScore > 0 && (
            <div className="mb-6 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
              <p className="font-semibold">GPU性能目安: ベンチマークスコア {benchmarkFloorScore.toLocaleString("ja-JP")} 以上</p>
              <p className="mt-1 text-xs text-emerald-800">
                選択GPUスコア: {selectedGpuBenchmarkScore.toLocaleString("ja-JP")}
                {selectedGpuBenchmarkScore >= benchmarkFloorScore ? " (基準達成)" : " (候補不足のため未達)"}
              </p>
              {selectedGpuGamingTierLabel && (
                <p className="mt-1 text-xs text-emerald-800">GPU帯: {selectedGpuGamingTierLabel}</p>
              )}
            </div>
          )}

          {configurationId && (
            <p className="text-sm text-gray-500 mb-6">
              {isSavedConfiguration(config) ? "保存済み構成ID" : "新規生成ID"}: {configurationId}
            </p>
          )}

          {isSavedConfiguration(config) && (
            <p className="text-sm text-gray-500 -mt-4 mb-6">
              保存日時: {new Date(config.created_at).toLocaleString("ja-JP")}
            </p>
          )}

          {isSavedConfiguration(config) && (
            <div className="bg-indigo-50 border border-indigo-300 rounded-lg p-4 mb-6">
              <p className="text-sm font-semibold text-indigo-900 mb-2">保存名</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  maxLength={120}
                  value={editingConfigurationName}
                  onChange={(e) => {
                    setEditingConfigurationName(e.target.value);
                    setNameUpdateError(null);
                  }}
                  placeholder="構成の名前（例: 9800X3D + RX 7600 構成）"
                  className="flex-1 border border-indigo-300 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
                <button
                  onClick={async () => {
                    setNameUpdateError(null);
                    setNameUpdateLoading(true);
                    try {
                      const updated = await (async () => {
                        const { updateSavedConfiguration } = await import("./api");
                        return await updateSavedConfiguration(config.id, {
                          name: editingConfigurationName.trim(),
                        });
                      })();
                      
                      // 保存成功時、親コンポーネントに通知
                      if (onSavedConfiguration) {
                        onSavedConfiguration(updated);
                      }
                      setNameUpdateError(null);
                    } catch (error) {
                      setNameUpdateError(error instanceof Error ? error.message : "保存名の更新に失敗しました。");
                    } finally {
                      setNameUpdateLoading(false);
                    }
                  }}
                  disabled={nameUpdateLoading || editingConfigurationName === (config.name || "")}
                  className="bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-400 text-white px-4 py-2 rounded-lg text-sm font-semibold transition-colors disabled:cursor-not-allowed"
                >
                  {nameUpdateLoading ? "更新中..." : "更新"}
                </button>
              </div>
              {nameUpdateError && (
                <p className="mt-2 text-xs text-red-700">{nameUpdateError}</p>
              )}
            </div>
          )}

          <div className="bg-blue-50 border-2 border-blue-300 rounded-lg p-6 mb-8">
            {hasBudgetCorrection && (
              <div className={`mb-3 rounded-md border px-3 py-2 text-xs font-semibold ${budgetCorrectionStyle}`}>
                <span className="mr-2 inline-flex items-center rounded-full border border-current px-2 py-0.5 text-[11px] leading-none">
                  {budgetCorrectionLabel}
                </span>
                <span>予算: {formatCurrency(requestedBudget)} → {formatCurrency(adjustedBudget)}</span>
              </div>
            )}
            <div className="flex justify-between items-center">
              <div>
                <p className="text-gray-600">指定予算</p>
                <p className="text-2xl font-bold text-gray-800">
                  {formatCurrency(requestedBudget)}
                </p>
              </div>
              <div className="text-3xl text-gray-400">→</div>
              <div>
                <p className="text-gray-600">構成金額</p>
                <p className="text-2xl font-bold text-green-600">
                  {formatCurrency(displayedTotalPrice)}
                </p>
              </div>
              <div className="text-right">
                <p className="text-gray-600">推定消費電力</p>
                <p className="text-2xl font-bold text-gray-800">
                  {estimatedPower}W
                </p>
              </div>
            </div>
          </div>

          {selectionSummary.coolerType && (
            <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 mb-8">
              <p className="text-sm font-semibold text-slate-700 mb-2">選択条件</p>
              <div className="grid grid-cols-2 gap-2 text-sm text-slate-600">
                <div>クーラー方式: <span className="font-semibold text-slate-800">{selectionSummary.coolerType}</span></div>
                <div>ラジエーター: <span className="font-semibold text-slate-800">{selectionSummary.radiatorSize ?? "指定なし"}</span></div>
                <div>クーラー方針: <span className="font-semibold text-slate-800">{selectionSummary.coolingProfile ?? "指定なし"}</span></div>
                <div>ケースサイズ: <span className="font-semibold text-slate-800">{selectionSummary.caseSize ?? "指定なし"}</span></div>
                <div>ケースファン方針: <span className="font-semibold text-slate-800">{selectionSummary.caseFanPolicy ?? "指定なし"}</span></div>
                <div>CPUメーカー: <span className="font-semibold text-slate-800">{selectionSummary.cpuVendor ?? "指定なし"}</span></div>
                <div>構成方針: <span className="font-semibold text-slate-800">{selectionSummary.buildPriority ?? "指定なし"}</span></div>
              </div>
            </div>
          )}

          <div className="mb-8 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-indigo-700">GPU性能比較</p>
                <p className="text-xs text-indigo-600">
                  {currentGpuPart ? `選択中GPU: ${currentGpuPart.name}` : "GPU比較対象は見つかりませんでした。"}
                </p>
                {usageCode === "creator" && (
                  <p className="mt-1 text-xs text-indigo-600">
                    クリエイターPCではVRAM容量を優先し、同条件ならNVIDIAを優先します。NVIDIA対応アプリが多く、高解像度編集や重い3D素材向けの選定です。
                  </p>
                )}
              </div>
              {gpuSnapshot && (
                <p className="text-xs text-indigo-600">
                  Snapshot #{gpuSnapshot.id} / {gpuSnapshot.source_name}
                </p>
              )}
            </div>

            {gpuComparisonLoading ? (
              <p className="text-sm text-indigo-700">GPU性能データを読み込み中です…</p>
            ) : gpuComparisonError ? (
              <p className="text-sm font-medium text-rose-700">{gpuComparisonError}</p>
            ) : gpuComparison ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-separate border-spacing-0 text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wide text-indigo-600">
                      <th className="border-b border-indigo-200 pb-2 pr-4">順位</th>
                      <th className="border-b border-indigo-200 pb-2 pr-4">モデル</th>
                      <th className="border-b border-indigo-200 pb-2 pr-4">VRAM</th>
                      <th className="border-b border-indigo-200 pb-2 pr-4">性能スコア</th>
                      <th className="border-b border-indigo-200 pb-2">詳細</th>
                    </tr>
                  </thead>
                  <tbody>
                    {gpuComparison.results
                      .slice()
                      .sort((left, right) => left.rank_global - right.rank_global)
                      .slice(0, RANKING_DISPLAY_LIMIT)
                      .map((entry) => {
                        const isCurrent = entry.model_key === currentGpuModelKey;
                        return (
                          <tr key={entry.model_key} className={isCurrent ? "bg-indigo-100/80" : "bg-white/70"}>
                            <td className="border-b border-indigo-100 py-2 pr-4 font-semibold text-slate-700">
                              #{entry.rank_global}
                            </td>
                            <td className="border-b border-indigo-100 py-2 pr-4 font-medium text-slate-800">
                              {formatGpuModelLabel(entry)}
                              {isCurrent && (
                                <span className="ml-2 rounded-full bg-indigo-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                                  現在の構成
                                </span>
                              )}
                            </td>
                            <td className="border-b border-indigo-100 py-2 pr-4 text-slate-700">
                              {entry.vram_gb ? `${entry.vram_gb}GB` : "-"}
                            </td>
                            <td className="border-b border-indigo-100 py-2 pr-4 text-slate-700">
                              {entry.perf_score.toLocaleString("ja-JP")}
                            </td>
                            <td className="border-b border-indigo-100 py-2 text-slate-700">
                              <a
                                href={entry.detail_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="font-medium text-indigo-700 hover:text-indigo-900"
                              >
                                表示
                              </a>
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-indigo-700">GPU比較データはまだありません。</p>
            )}

            {gpuComparison?.missing_models?.length ? (
              <p className="mt-3 text-xs text-slate-600">
                見つからなかったモデル: {gpuComparison.missing_models.join(", ")}
              </p>
            ) : null}
          </div>

          <div className="mb-8 rounded-lg border border-emerald-200 bg-emerald-50 p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-emerald-700">
                  {isGamingUsage
                    ? gamingCpuRankingMode === "cost"
                      ? "ゲーミングCPU選択テーブル（AMD・コスパ重視）"
                      : "ゲーミングCPU順位（AMD・スペック順）"
                    : "CPU選考資料（AMD/Intel）"}
                </p>
                {creatorCpuRecommendationText && (
                  <p className="mt-1 text-xs text-emerald-600">{creatorCpuRecommendationText}</p>
                )}
                <p className="text-xs text-emerald-600">
                  {currentCpuPart
                    ? `選択中CPU: ${currentCpuPart.name}`
                    : isGamingUsage
                      ? "AMDのみで順位付けしています。"
                      : "CPU比較対象は見つかりませんでした。"}
                </p>
              </div>
              {cpuMaterialMeta && (
                <p className="text-xs text-emerald-600">
                  {isGamingUsage ? "元データ" : "件数"}: {cpuMaterialMeta.entry_count} / 除外: {cpuMaterialMeta.excluded_count}
                </p>
              )}
            </div>

            {isGamingUsage ? (
              <p className="mb-3 text-xs text-emerald-600">
                {gamingCpuRankingMode === "cost"
                  ? "コスパ重視では性能/価格で選択候補を並べています。"
                  : "スペック重視ではX3Dを優先して性能順に並べています。"}
              </p>
            ) : null}

            {cpuComparisonLoading ? (
              <p className="text-sm text-emerald-700">CPU選考資料を読み込み中です…</p>
            ) : cpuComparisonError ? (
              <p className="text-sm font-medium text-rose-700">{cpuComparisonError}</p>
            ) : cpuComparison ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] border-separate border-spacing-0 text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wide text-emerald-700">
                      <th className="border-b border-emerald-200 pb-2 pr-4">順位</th>
                      <th className="border-b border-emerald-200 pb-2 pr-4">モデル</th>
                      <th className="border-b border-emerald-200 pb-2 pr-4">Vendor</th>
                      <th className="border-b border-emerald-200 pb-2 pr-4">{gamingCpuRankingMode === "cost" ? "コスパ" : "性能目安"}</th>
                      <th className="border-b border-emerald-200 pb-2">詳細</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cpuComparison.results.slice(0, RANKING_DISPLAY_LIMIT).map((entry, index) => {
                        const isCurrent = currentCpuModelKeyNormalized
                          ? normalizeCpuModelKey(entry.model_name) === currentCpuModelKeyNormalized
                          : false;
                        return (
                          <tr key={`${entry.vendor}:${entry.model_name}`} className={isCurrent ? "bg-emerald-100/80" : "bg-white/70"}>
                            <td className="border-b border-emerald-100 py-2 pr-4 font-semibold text-slate-700">{index + 1}</td>
                            <td className="border-b border-emerald-100 py-2 pr-4 font-medium text-slate-800">
                              {formatCpuModelLabel(entry)}
                              {isCurrent && (
                                <span className="ml-2 rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                                  現在の構成
                                </span>
                              )}
                            </td>
                            <td className="border-b border-emerald-100 py-2 pr-4 text-slate-700">{entry.vendor.toUpperCase()}</td>
                            <td className="border-b border-emerald-100 py-2 pr-4 text-slate-700">
                              {gamingCpuRankingMode === "cost"
                                ? (entry.value_score ?? 0).toFixed(6)
                                : entry.perf_score.toLocaleString("ja-JP")}
                            </td>
                            <td className="border-b border-emerald-100 py-2 text-slate-700">
                              <a href={entry.source_url} target="_blank" rel="noopener noreferrer" className="font-medium text-emerald-700 hover:text-emerald-900">
                                表示
                              </a>
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-emerald-700">CPU選考資料データはまだありません。</p>
            )}

            {cpuMaterialMeta?.exclude_intel_13_14 ? (
              <p className="mt-3 text-xs text-slate-600">Intel 13世代/14世代は除外して集計しています。</p>
            ) : null}
          </div>

          <div className="space-y-4">
            <h3 className="text-2xl font-bold text-gray-800">PC構成</h3>
            {activeDisplayParts.map((part, index) => {
              const isIgpu = part.category === "gpu" && part.price === 0 && part.name.includes("内蔵");
              const isUnselectedOptionalStorage = (part.category === "storage2" || part.category === "storage3") && Boolean(part.isPlaceholder);
              const isUnselectedCpuCooler = part.category === "cpu_cooler" && Boolean(part.isPlaceholder);
              const isCaseWithoutIncludedFans = part.category === "case" && Number(part.specs?.included_fan_count ?? -1) === 0;
              const categoryLabel = PART_CATEGORY_LABELS[part.category] ?? part.category;
              const psuCapacityWatts = part.category === "psu" ? getPsuWattage(part) : null;
              const memoryCapacityGb = part.category === "memory" ? inferMemoryCapacityGb(part) : 0;
              const memoryModuleCount = part.category === "memory" ? inferMemoryModuleCount(part) : 0;
              const candidatePartType = resolveCandidatePartType(part.category);
              const candidates = partCandidatesByCategory[candidatePartType] ?? [];
              const isEditorOpen = openEditorCategory === part.category;
              const makerOptions = Array.from(new Set(candidates.map((candidate) => inferManufacturerName({ name: candidate.name, specs: candidate.specs })))).sort((left, right) => left.localeCompare(right, "ja"));
              const minPriceValue = Number(candidateMinPrice);
              const maxPriceValue = Number(candidateMaxPrice);
              const normalizedQuery = candidateQuery.trim().toLowerCase();
              const requiredPsuWatt = Math.ceil(estimatedPower * 1.25);
              const filteredCandidates = candidates
                .filter((candidate) => {
                  if (part.category === "storage") {
                    const mediaType = inferStorageMediaTypeFromPart({
                      name: candidate.name,
                      specs: candidate.specs ?? null,
                    });
                    if (mediaType !== "ssd") {
                      return false;
                    }
                  }
                  if (normalizedQuery && !candidate.name.toLowerCase().includes(normalizedQuery)) {
                    return false;
                  }
                  const maker = inferManufacturerName({ name: candidate.name, specs: candidate.specs });
                  if (candidateMaker !== "all" && maker !== candidateMaker) {
                    return false;
                  }
                  if (!ignorePriceRange) {
                    if (candidateMinPrice && Number.isFinite(minPriceValue) && candidate.price < minPriceValue) {
                      return false;
                    }
                    if (candidateMaxPrice && Number.isFinite(maxPriceValue) && candidate.price > maxPriceValue) {
                      return false;
                    }
                  }
                  return true;
                });
              const hasCollapsedCandidates = filteredCandidates.length > CANDIDATE_COLLAPSE_LIMIT;
              const visibleCandidates = hasCollapsedCandidates && !showAllCandidates
                ? filteredCandidates.slice(0, CANDIDATE_COLLAPSE_LIMIT)
                : filteredCandidates;
              
              // 付属CPUクーラーコメントをcpu_coolerセクションに表示
              if (showBundledCpuCoolerNote && part.category === "cpu_cooler") {
                return (
                  <div key={index} className="rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-900">
                    <p className="font-semibold">付属CPUクーラーを使用</p>
                    <p className="mt-1 text-xs text-sky-800">
                      CPUクーラーは未選択ですが、CPU付属クーラーを前提にしています。
                    </p>
                  </div>
                );
              }
              
              return (
                <div
                  key={index}
                  className={`border rounded-lg p-4 transition ${
                    isIgpu
                      ? "border-green-200 bg-green-50"
                      : "border-gray-200 hover:shadow-md"
                  }`}
                >
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <p className="text-sm font-semibold text-gray-500">
                        {categoryLabel}
                      </p>
                      <p className="text-lg font-bold text-gray-800">
                        {part.name?.trim() ? part.name : "未選択"}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-2">
                      {isIgpu ? (
                        <span className="inline-block bg-green-100 text-green-700 text-xs font-semibold px-2 py-1 rounded">
                          内蔵GPU
                        </span>
                      ) : isUnselectedOptionalStorage || isUnselectedCpuCooler ? (
                        <span className="inline-block bg-slate-100 text-slate-600 text-xs font-semibold px-2 py-1 rounded">
                          任意
                        </span>
                      ) : (
                        <p className="text-lg font-bold text-indigo-600">
                          {formatCurrency(part.price)}
                        </p>
                      )}

                      <button
                        type="button"
                        onClick={() => {
                          void openPartEditor(part.category);
                        }}
                        className="rounded border border-indigo-300 bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
                      >
                        {categoryLabel}を変更
                      </button>
                    </div>
                  </div>

                  {part.category === "memory" && memoryCapacityGb > 0 && (
                    <p className="mb-2 text-xs text-slate-600">
                      合計容量: <span className="font-semibold text-slate-800">{memoryCapacityGb}GB</span>
                      {memoryModuleCount > 1 && (
                        <span className="ml-2">({Math.max(1, Math.floor(memoryCapacityGb / memoryModuleCount))}GB x {memoryModuleCount})</span>
                      )}
                    </p>
                  )}

                  {isCaseWithoutIncludedFans && (
                    <div className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                      このケースは付属ファンなしのため、別途ケースファンの追加を推奨します。
                    </div>
                  )}
                  {!isIgpu && !isUnselectedOptionalStorage && part.url && (
                    <a
                      href={part.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-500 hover:text-blue-700 text-sm font-medium inline-flex items-center"
                    >
                      購入ページを見る →
                    </a>
                  )}
                  {part.category === "psu" && psuCapacityWatts !== null && psuCapacityWatts > 1000 && (
                    <p className="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                      1000Wを超える電源容量のため、コンセント側の工事が必要になる可能性があります。
                    </p>
                  )}
                  {isIgpu && (
                    <p className="text-xs text-green-600">
                      CPU内蔵グラフィックスを使用します。別途GPUは不要です。
                    </p>
                  )}

                  {isEditorOpen && (
                    <div className="mt-3 rounded-md border border-indigo-200 bg-indigo-50 p-3">
                      <p className="mb-2 text-xs font-semibold text-indigo-800">{categoryLabel}の候補から選択</p>
                      <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-2">
                        <input
                          value={candidateQuery}
                          onChange={(event) => setCandidateQuery(event.target.value)}
                          placeholder="候補名で検索"
                          className="rounded border border-indigo-200 bg-white px-2 py-1 text-xs text-slate-800"
                        />
                        <select
                          value={candidateMaker}
                          onChange={(event) => setCandidateMaker(event.target.value)}
                          className="rounded border border-indigo-200 bg-white px-2 py-1 text-xs text-slate-800"
                        >
                          <option value="all">メーカー: すべて</option>
                          {makerOptions.map((maker) => (
                            <option key={maker} value={maker}>{maker}</option>
                          ))}
                        </select>
                        <input
                          value={candidateMinPrice}
                          onChange={(event) => setCandidateMinPrice(event.target.value.replace(/[^0-9]/g, ""))}
                          placeholder="最低価格"
                          className="rounded border border-indigo-200 bg-white px-2 py-1 text-xs text-slate-800"
                        />
                        <input
                          value={candidateMaxPrice}
                          onChange={(event) => setCandidateMaxPrice(event.target.value.replace(/[^0-9]/g, ""))}
                          placeholder="最高価格"
                          className="rounded border border-indigo-200 bg-white px-2 py-1 text-xs text-slate-800"
                        />
                      </div>
                      <div className="mb-3 flex flex-wrap items-center gap-3 text-[11px] text-slate-700">
                        <label className="inline-flex items-center gap-1">
                          <input type="checkbox" checked={ignorePriceRange} onChange={(event) => setIgnorePriceRange(event.target.checked)} />
                          価格帯を無視して表示
                        </label>
                        <span>
                          表示件数: {visibleCandidates.length} / {filteredCandidates.length}（非互換はグレー表示）
                        </span>
                      </div>
                      {partCandidatesLoading ? (
                        <p className="text-xs text-indigo-700">候補を読み込み中です…</p>
                      ) : partCandidatesError ? (
                        <p className="text-xs text-rose-700">{partCandidatesError}</p>
                      ) : filteredCandidates.length === 0 ? (
                        <p className="text-xs text-indigo-700">候補が見つかりませんでした。</p>
                      ) : (
                        <div className="space-y-2">
                          {visibleCandidates.map((candidate) => {
                            const compatibility = checkPartCompatibility(part.category, candidate, activeDisplayParts, requiredPsuWatt);
                            const maker = inferManufacturerName({ name: candidate.name, specs: candidate.specs });
                            return (
                              <button
                                key={`${candidate.id}-${part.category}`}
                                type="button"
                                onClick={() => {
                                  if (!compatibility.ok) {
                                    setPendingIncompatibleSelection({
                                      category: part.category,
                                      candidate,
                                      reasons: compatibility.reasons,
                                    });
                                    return;
                                  }
                                  applyManualPartSelection(part.category, candidate);
                                }}
                                className={`w-full rounded border px-3 py-2 text-left ${compatibility.ok ? "border-indigo-200 bg-white hover:bg-indigo-100" : "border-slate-300 bg-slate-100 text-slate-500 hover:bg-slate-200"}`}
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <span className="mr-4 text-xs">{candidate.name}</span>
                                  <span className={`text-xs font-semibold ${compatibility.ok ? "text-indigo-700" : "text-slate-600"}`}>{formatCurrency(candidate.price)}</span>
                                </div>
                                <div className="mt-1 flex items-center justify-between gap-2 text-[11px]">
                                  <span>メーカー: {maker}</span>
                                  <span className={compatibility.ok ? "text-emerald-700" : "text-slate-600"}>{compatibility.ok ? "互換性OK" : "非互換候補"}</span>
                                </div>
                                {!compatibility.ok && compatibility.reasons.length > 0 && (
                                  <p className="mt-1 text-[11px] font-medium text-slate-600">警告: {compatibility.reasons.join(" ")}</p>
                                )}
                              </button>
                            );
                          })}
                          {hasCollapsedCandidates && (
                            <button
                              type="button"
                              onClick={() => setShowAllCandidates((previous) => !previous)}
                              className="w-full rounded border border-indigo-300 bg-white px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
                            >
                              {showAllCandidates
                                ? "候補を折りたたむ"
                                : `候補をさらに表示（残り ${filteredCandidates.length - visibleCandidates.length} 件）`}
                            </button>
                          )}
                        </div>
                      )}

                      {(part.category === "storage2" || part.category === "storage3" || part.category === "cpu_cooler") && !part.isPlaceholder && (
                        <button
                          type="button"
                          onClick={() => {
                            unsetOptionalPart(part.category as "storage2" | "storage3" | "cpu_cooler");
                          }}
                          className="mt-3 rounded border border-slate-300 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-100"
                        >
                          未選択に戻す
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {pendingIncompatibleSelection && (
            <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-900/45 p-4">
              <div className="w-full max-w-lg rounded-xl bg-white p-5 shadow-2xl">
                <h4 className="text-base font-bold text-slate-900">非互換候補を選択しますか？</h4>
                <p className="mt-1 text-sm text-slate-600">
                  {PART_CATEGORY_LABELS[pendingIncompatibleSelection.category] ?? pendingIncompatibleSelection.category}: {pendingIncompatibleSelection.candidate.name}
                </p>
                <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
                  {pendingIncompatibleSelection.reasons.map((reason, index) => (
                    <p key={`${reason}-${index}`}>・{reason}</p>
                  ))}
                </div>
                <div className="mt-4 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setPendingIncompatibleSelection(null)}
                    className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
                  >
                    キャンセル
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      applyManualPartSelection(
                        pendingIncompatibleSelection.category,
                        pendingIncompatibleSelection.candidate,
                      );
                      setPendingIncompatibleSelection(null);
                    }}
                    className="rounded bg-amber-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-amber-700"
                  >
                    この候補を選択
                  </button>
                </div>
              </div>
            </div>
          )}

        </div>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl gap-3 p-3 md:p-4">
          <button
            onClick={handleExportPdf}
            className="flex-1 rounded-lg bg-emerald-600 px-4 py-3 font-bold text-white transition hover:bg-emerald-700"
          >
            🖨 見積書PDF保存
          </button>
          <button
            onClick={onBack}
            className="flex-1 rounded-lg bg-indigo-600 px-4 py-3 font-bold text-white transition hover:bg-indigo-700"
          >
            別の構成を生成
          </button>
        </div>
      </div>
    </div>
  );
}

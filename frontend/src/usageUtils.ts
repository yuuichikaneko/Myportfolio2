import type { UsageCode } from "./api";

export type NormalizedUsageCode = "gaming" | "general" | "creator" | "business" | "workstation";

export function normalizeUsageCode(
  usage: string,
  fallback: NormalizedUsageCode | "all" = "all"
): NormalizedUsageCode | "all" {
  if (usage === "video_editing" || usage === "create") {
    return "creator";
  }
  if (usage === "ai") {
    return "workstation";
  }
  if (usage === "standard") {
    return "general";
  }
  if (usage === "gaming" || usage === "general" || usage === "creator" || usage === "business" || usage === "workstation") {
    return usage;
  }
  return fallback;
}

export function isNormalizedUsageCode(value: string): value is UsageCode {
  return (
    value === "gaming" ||
    value === "general" ||
    value === "creator" ||
    value === "business" ||
    value === "workstation"
  );
}
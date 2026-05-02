import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigForm } from "./ConfigForm";

const apiMocks = vi.hoisted(() => ({
  getMarketPriceRangeMock: vi.fn(),
  getPartPriceRangesMock: vi.fn(),
  getStorageInventoryMock: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    getMarketPriceRange: apiMocks.getMarketPriceRangeMock,
    getPartPriceRanges: apiMocks.getPartPriceRangesMock,
    getStorageInventory: apiMocks.getStorageInventoryMock,
  };
});

describe("ConfigForm presets", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    apiMocks.getMarketPriceRangeMock.mockResolvedValue({
      min: 100000,
      max: 400000,
      default: 250000,
      currency: "JPY",
      gaming_x3d_recommended_min: 184980,
      sources: {},
    });

    apiMocks.getPartPriceRangesMock.mockResolvedValue({
      gpu: { label: "GPU", min: 30000, max: 80000, avg: 55000, count: 2 },
    });

    apiMocks.getStorageInventoryMock.mockResolvedValue({
      total_count: 1,
      latest_updated_at: "2026-04-05T00:00:00Z",
      interface_summary: [
        { interface: "nvme", label: "NVMe", count: 1, min_price: 10000, max_price: 10000, avg_price: 10000 },
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
              updated_at: "2026-04-05T00:00:00Z",
            },
          ],
        },
      ],
    });
  });

  it("keeps low-end preset selected after switching to spec priority", async () => {
    render(<ConfigForm onSubmit={() => undefined} isLoading={false} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "ローエンド" })).toBeInTheDocument();
    });

    const lowEndButton = screen.getByRole("button", { name: "ローエンド" });
    await userEvent.click(lowEndButton);
    expect(lowEndButton.className).toContain("bg-blue-700");

    await userEvent.click(screen.getByRole("button", { name: "スペック重視" }));

    expect(screen.getByRole("button", { name: "ローエンド" }).className).toContain("bg-blue-700");
  });

  it("keeps middle and high-end presets selected after switching build priority", async () => {
    render(<ConfigForm onSubmit={() => undefined} isLoading={false} />);

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "ミドル" }).length).toBeGreaterThan(0);
    });

    for (const presetLabel of ["ミドル", "ハイエンド"] as const) {
      await userEvent.click(screen.getByRole("button", { name: "コスト重視" }));

      const presetButtons = screen.getAllByRole("button", { name: presetLabel });
      const presetButton = presetButtons[presetButtons.length - 1];
      await userEvent.click(presetButton);
      expect(
        screen
          .getAllByRole("button", { name: presetLabel })
          .some((button) => button.className.includes("bg-blue-700"))
      ).toBe(true);

      await userEvent.click(screen.getByRole("button", { name: "スペック重視" }));
      expect(
        screen
          .getAllByRole("button", { name: presetLabel })
          .some((button) => button.className.includes("bg-blue-700"))
      ).toBe(true);

      await userEvent.click(screen.getByRole("button", { name: "コスト重視" }));
      expect(
        screen
          .getAllByRole("button", { name: presetLabel })
          .some((button) => button.className.includes("bg-blue-700"))
      ).toBe(true);
    }
  });

  it("raises the creator spec premium preset to a 5090-capable budget", async () => {
    render(<ConfigForm onSubmit={() => undefined} isLoading={false} />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /クリエイターPC/ })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("radio", { name: /クリエイターPC/ }));
    await userEvent.click(screen.getByRole("button", { name: "スペック重視" }));

    const premiumButtons = screen.getAllByRole("button", { name: "プレミアム" });
    const premiumButton = premiumButtons[premiumButtons.length - 1];
    await userEvent.click(premiumButton);

    expect(screen.getByRole("spinbutton")).toHaveValue(1314478);
  });

  it("keeps the general middle preset above the low-end tier", async () => {
    render(<ConfigForm onSubmit={() => undefined} isLoading={false} />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /汎用PC/ })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("radio", { name: /汎用PC/ }));
    const middleButtons = screen.getAllByRole("button", { name: "ミドル" });
    const middleButton = middleButtons[0];
    await userEvent.click(middleButton);

    expect(screen.getByRole("spinbutton")).toHaveValue(224980);
    expect(screen.getByRole("spinbutton")).not.toHaveValue(85000);
  });
});

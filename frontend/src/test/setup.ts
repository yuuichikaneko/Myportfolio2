import "@testing-library/jest-dom/vitest";

// jsdom は window.scrollTo を実装していないため、テスト内でのノイズ警告を抑制する
window.scrollTo = () => undefined;

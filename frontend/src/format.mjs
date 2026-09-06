export const factors = ["growth", "valuation", "quality", "momentum", "risk"];
export const number = (value, digits = 1) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "Unavailable";
export const percent = (value, digits = 1) =>
  typeof value === "number" && Number.isFinite(value)
    ? number(value * 100, digits) + "%"
    : "Unavailable";
export const money = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? "$" + number(value, 2)
    : "Unavailable";
export const label = (value) =>
  String(value ?? "Unavailable")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export function safeURL(value) {
  try {
    const u = new URL(value);
    return ["http:", "https:"].includes(u.protocol) ? u.href : null;
  } catch {
    return null;
  }
}
export function pairOptions(candidates, first, second) {
  const tickers = candidates.map((r) => r.ticker);
  const left = tickers.includes(first) ? first : tickers[0];
  const alternatives = tickers.filter((t) => t !== left);
  return {
    left,
    right: alternatives.includes(second) ? second : alternatives[0],
    alternatives,
  };
}

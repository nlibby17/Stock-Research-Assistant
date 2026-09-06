import test from "node:test";
import assert from "node:assert/strict";
import {
  number,
  percent,
  money,
  pairOptions,
  safeURL,
} from "../src/format.mjs";
test("missing data is not zero", () => {
  for (const value of [null, undefined, NaN, Infinity, ""]) {
    assert.equal(number(value), "Unavailable");
    assert.equal(percent(value), "Unavailable");
  }
  assert.equal(number(0), "0.0");
  assert.equal(percent(1, 0), "100%");
  assert.equal(money(1234.5), "$1,234.50");
});
test("pair selection never compares a stock against itself", () => {
  const candidates = [{ ticker: "A" }, { ticker: "B" }, { ticker: "C" }];
  assert.deepEqual(pairOptions(candidates, "B", "B"), {
    left: "B",
    right: "A",
    alternatives: ["A", "C"],
  });
  assert.equal(pairOptions(candidates, "invalid", "C").right, "C");
  assert.equal(pairOptions([]).right, undefined);
});
test("links reject active content and local files", () => {
  for (const url of [
    "javascript:alert(1)",
    "data:text/html,test",
    "file:///private",
    "/api/report",
  ])
    assert.equal(safeURL(url), null);
  assert.equal(safeURL("https://www.sec.gov/"), "https://www.sec.gov/");
});

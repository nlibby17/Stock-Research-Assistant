import { build } from "esbuild";
import { writeFile, mkdir, readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const out = new URL("../src/stockrank/web_assets/", import.meta.url);
await mkdir(out, { recursive: true });
// Ship full dependency notices with the prebuilt bundle, not just minifier comments.
const lock = JSON.parse(
  await readFile(new URL("./package-lock.json", import.meta.url), "utf8"),
);
const notices = [];
for (const [path, pkg] of Object.entries(lock.packages)) {
  if (!path || pkg.dev || !path.startsWith("node_modules/")) continue;
  const directory = new URL("./" + path + "/", import.meta.url);
  const files = (await readdir(directory))
    .filter((name) => /^(licen[cs]e|copying|notice)(\.|$)/i.test(name))
    .sort();
  if (!files.length) throw new Error("Missing dependency license: " + path);
  notices.push(path.replace(/^node_modules\//, "") + " @ " + pkg.version);
  for (const name of files)
    notices.push(await readFile(new URL(name, directory), "utf8"));
}
await writeFile(
  new URL("THIRD_PARTY_NOTICES.txt", out),
  notices.join("\n\n---\n\n"),
);
await build({
  entryPoints: [fileURLToPath(new URL("./src/app.jsx", import.meta.url))],
  bundle: true,
  minify: true,
  sourcemap: false,
  legalComments: "external",
  target: ["chrome100", "safari15"],
  outfile: fileURLToPath(new URL("app.js", out)),
  define: { "process.env.NODE_ENV": '"production"' },
});
await writeFile(
  new URL("index.html", out),
  '<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>Stock Research Assistant</title><link rel="stylesheet" href="/app.css"></head><body><div id="root"></div><script type="module" src="/app.js"></script></body></html>\n',
);

#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "..");
const resultsDir = path.join(repoRoot, "results");
const gasLogPath = path.join(resultsDir, "gas_log.jsonl");
const outPath = path.join(resultsDir, "gas_summary.json");

function stddev(values) {
  if (values.length <= 1) return 0;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / values.length;
  return Math.sqrt(variance);
}

if (!fs.existsSync(gasLogPath)) {
  fs.mkdirSync(resultsDir, { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify({}, null, 2));
  console.log(JSON.stringify({}, null, 2));
  process.exit(0);
}

const buckets = {};
for (const line of fs.readFileSync(gasLogPath, "utf8").split(/\r?\n/).filter(Boolean)) {
  const row = JSON.parse(line);
  const fn = row.function || "unknown";
  buckets[fn] ||= [];
  buckets[fn].push(Number(row.gasUsed || 0));
}

const summary = {};
for (const [fn, values] of Object.entries(buckets)) {
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  summary[fn] = {
    count: values.length,
    mean_gas_used: Number(mean.toFixed(3)),
    stddev_gas_used: Number(stddev(values).toFixed(3)),
  };
}

fs.mkdirSync(resultsDir, { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));

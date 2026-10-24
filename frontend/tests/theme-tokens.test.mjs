/**
 * 主题系统架构守卫：
 * 1) tokens.css 必须为 4 预设 × 2 模式提供完整 token（含旧变量别名，保证存量组件可换肤）；
 * 2) style.css 全局样式不得出现硬编码 hex（取色一律走 var(--token)）；
 * 3) 组件 <style> 块不得出现硬编码 hex（白名单见 ALLOWED_HEX_FILES）。
 * 目的：防止后续开发把固定色值复制回页面，破坏「晨雾青绿/极夜」等主题切换。
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import assert from "node:assert/strict";

const ROOT = join(import.meta.dirname, "..");
const SRC = join(ROOT, "src");

const PRESETS = ["mist-jade", "cloud-blue", "cream-apricot", "polar-night"];
const MODES = ["light", "dark"];
/** 每个主题块必须定义的核心 token（含旧名别名） */
const REQUIRED_VARS = [
  "--bg", "--bg-panel", "--bg-panel-2", "--bg-hover",
  "--border", "--border-soft", "--text", "--text-dim",
  "--accent", "--accent-2", "--up", "--down", "--warn",
  "--shadow", "--sidebar-bg", "--table-head-bg",
];

function read(path) {
  return readFileSync(join(SRC, path), "utf8");
}

test("tokens.css 为全部预设×模式提供完整 token", () => {
  const css = read("styles/tokens.css");
  for (const preset of PRESETS) {
    for (const mode of MODES) {
      // 默认态可由预设级选择器承载（如 mist-jade 之 light、polar-night 之 dark），非默认态必须显式 mode
      const candidates = [
        `[data-wl-theme="${preset}"]`,
        `[data-wl-theme="${preset}"][data-wl-mode="${mode}"]`,
      ];
      let found = -1;
      let usedSelector = "";
      for (const selector of candidates) {
        found = css.indexOf(selector);
        if (found >= 0) { usedSelector = selector; break; }
      }
      assert.ok(found >= 0, `缺少主题块选择器：${candidates.join(" 或 ")}`);
      const nextBrace = css.indexOf("{", found);
      const end = css.indexOf("\n}", nextBrace);
      const block = css.slice(nextBrace, end);
      for (const token of REQUIRED_VARS) {
        assert.ok(
          block.includes(`${token}:`),
          `${usedSelector} 缺少 ${token}`,
        );
      }
    }
  }
});

test("style.css 不含硬编码 hex（全部走 token）", () => {
  const css = read("style.css");
  const hexMatches = css.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
  assert.deepEqual(hexMatches, [], `style.css 出现硬编码色值：${hexMatches.join(", ")}`);
  // 且确实消费 token
  for (const token of ["var(--bg-panel)", "var(--accent)", "var(--border)", "var(--text-dim)"]) {
    assert.ok(css.includes(token), `style.css 未引用 ${token}`);
  }
});

const ALLOWED_HEX_FILES = new Set([
  // 白名单：状态徽章白字（语义固定，与主题无关）
  "components/global/HealthChip.vue",
]);

function listVueFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...listVueFiles(full));
    else if (name.endsWith(".vue")) out.push(full);
  }
  return out;
}

test("组件 <style> 块不含硬编码 hex", () => {
  const offenders = [];
  for (const file of listVueFiles(SRC)) {
    const rel = file.slice(SRC.length + 1).replaceAll("\\", "/");
    if (ALLOWED_HEX_FILES.has(rel)) continue;
    const src = readFileSync(file, "utf8");
    const styleStart = src.lastIndexOf("<style");
    if (styleStart < 0) continue;
    const styleBlock = src.slice(styleStart);
    const hex = styleBlock.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
    if (hex.length) offenders.push(`${rel}: ${hex.join(", ")}`);
  }
  assert.deepEqual(offenders, [], `组件样式出现硬编码色值：\n${offenders.join("\n")}`);
});

test("主题 store 持久化键与导入导出格式约定存在", () => {
  const domain = read("domain/theme.ts");
  assert.ok(domain.includes("wl-theme-settings-v1"), "localStorage 键名变更需同步交接文档");
  assert.ok(domain.includes("normalizeSettings"), "导入需经 normalizeSettings 校验");
  const store = read("stores/theme.ts");
  assert.ok(store.includes("exportSettings"), "缺少主题导出");
  assert.ok(store.includes("importSettings"), "缺少主题导入");
});

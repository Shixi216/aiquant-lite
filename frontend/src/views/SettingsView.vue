<script setup lang="ts">
import { computed, ref } from "vue";
import PixelIcon from "../components/global/PixelIcon.vue";

import {
  CHART_PALETTES,
  DASHBOARD_WIDGETS,
  THEME_PRESETS,
  type ThemeSettings,
} from "../domain/theme";
import { useThemeStore } from "../stores/theme";

const themeStore = useThemeStore();
const settings = computed(() => themeStore.settings);

const importText = ref("");
const importMessage = ref("");
const exportCopied = ref(false);

const widgetList = computed(() =>
  settings.value.dashboardOrder
    .map((id) => DASHBOARD_WIDGETS.find((w) => w.id === id))
    .filter((w): w is (typeof DASHBOARD_WIDGETS)[number] => Boolean(w)),
);

function moveWidget(id: string, direction: -1 | 1) {
  const order = [...settings.value.dashboardOrder];
  const index = order.indexOf(id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= order.length) return;
  [order[index], order[target]] = [order[target]!, order[index]!];
  themeStore.update({ dashboardOrder: order });
}

function downloadExport() {
  const blob = new Blob([themeStore.exportSettings()], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "aiquant-lite-theme.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

async function copyExport() {
  try {
    await navigator.clipboard.writeText(themeStore.exportSettings());
    exportCopied.value = true;
    setTimeout(() => (exportCopied.value = false), 1600);
  } catch {
    exportCopied.value = false;
  }
}

function doImport() {
  importMessage.value = "";
  if (!importText.value.trim()) {
    importMessage.value = "请先粘贴主题 JSON 或选择文件";
    return;
  }
  try {
    themeStore.importSettings(importText.value);
    importMessage.value = "导入成功，已应用并保存到本地";
  } catch {
    importMessage.value = "导入失败：JSON 格式不合法";
  }
}

function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  void file.text().then((text) => {
    importText.value = text;
    doImport();
  });
  input.value = "";
}

const colorValue = (value: string | null) => value ?? "";

function setPrimary(event: Event) {
  const value = (event.target as HTMLInputElement).value;
  themeStore.update({ primaryColor: value || null });
}
function setSecondary(event: Event) {
  const value = (event.target as HTMLInputElement).value;
  themeStore.update({ secondaryColor: value || null });
}
function setPageBg(event: Event) {
  const value = (event.target as HTMLInputElement).value;
  themeStore.update({ pageBackground: value || null });
}

const shadowOptions: { value: ThemeSettings["shadowLevel"]; label: string }[] = [
  { value: 0, label: "无" },
  { value: 1, label: "柔和" },
  { value: 2, label: "明显" },
];
const densityOptions: { value: ThemeSettings["density"]; label: string }[] = [
  { value: "compact", label: "紧凑" },
  { value: "cozy", label: "舒适" },
  { value: "comfortable", label: "宽松" },
];
</script>

<template>
  <div class="settings-view">
    <p class="page-sub">
      所有设置实时生效并保存在浏览器本地（localStorage），仅含外观偏好，不涉及任何金融数据或凭据。
      主题切换会同步更新图表、表格、按钮、弹窗与侧边栏。
    </p>

    <section class="panel">
      <h3>模式与主题预设</h3>
      <div class="seg mode-seg" role="group" aria-label="浅色/深色模式">
        <button type="button" :class="{ active: settings.mode === 'light' }" @click="themeStore.update({ mode: 'light' })">☀️ 浅色模式</button>
        <button type="button" :class="{ active: settings.mode === 'dark' }" @click="themeStore.update({ mode: 'dark' })">🌙 深色模式</button>
      </div>
      <div class="preset-grid">
        <button
          v-for="preset in THEME_PRESETS"
          :key="preset.id"
          type="button"
          class="preset-card"
          :class="{ active: settings.preset === preset.id }"
          @click="themeStore.selectPreset(preset.id)"
        >
          <span class="preset-swatch" :style="{ background: preset.swatch[0] }">
            <span class="preset-swatch-accent" :style="{ background: preset.swatch[1] }"></span>
            <span class="preset-swatch-card" :style="{ background: preset.swatch[2] }"></span>
          </span>
          <span class="preset-name">{{ preset.name }}</span>
          <span class="preset-desc">{{ preset.description }}</span>
          <span v-if="settings.preset === preset.id" class="badge action">使用中</span>
        </button>
      </div>
    </section>

    <section class="panel">
      <h3>颜色</h3>
      <div class="field-row">
        <label class="field">
          <span class="field-label">全局主色</span>
          <span class="field-control">
            <input type="color" :value="settings.primaryColor ?? '#0f9b84'" @input="setPrimary" />
            <input type="text" :value="colorValue(settings.primaryColor)" placeholder="跟随预设" maxlength="9" @change="setPrimary" />
            <button v-if="settings.primaryColor" class="mini" type="button" @click="themeStore.update({ primaryColor: null })">跟随预设</button>
          </span>
        </label>
        <label class="field">
          <span class="field-label">辅助色</span>
          <span class="field-control">
            <input type="color" :value="settings.secondaryColor ?? '#35a2b8'" @input="setSecondary" />
            <input type="text" :value="colorValue(settings.secondaryColor)" placeholder="跟随预设" maxlength="9" @change="setSecondary" />
            <button v-if="settings.secondaryColor" class="mini" type="button" @click="themeStore.update({ secondaryColor: null })">跟随预设</button>
          </span>
        </label>
        <label class="field">
          <span class="field-label">页面背景</span>
          <span class="field-control">
            <input type="color" :value="settings.pageBackground ?? '#f3f7f5'" @input="setPageBg" />
            <input type="text" :value="colorValue(settings.pageBackground)" placeholder="跟随预设" maxlength="9" @change="setPageBg" />
            <button v-if="settings.pageBackground" class="mini" type="button" @click="themeStore.update({ pageBackground: null })">跟随预设</button>
          </span>
        </label>
      </div>
      <p class="hint">自定义主色会同步派生按钮、链接、激活态、图表首色与高亮描边；恢复"跟随预设"即还原当前主题原色。</p>
    </section>

    <section class="panel">
      <h3>卡片与质感</h3>
      <div class="field-row">
        <label class="field">
          <span class="field-label">卡片圆角：{{ settings.radius }}px</span>
          <input type="range" min="4" max="22" step="1" :value="settings.radius" @change="themeStore.update({ radius: Number(($event.target as HTMLInputElement).value) })" />
        </label>
        <label class="field">
          <span class="field-label">阴影强度</span>
          <span class="seg">
            <button v-for="option in shadowOptions" :key="option.value" type="button" :class="{ active: settings.shadowLevel === option.value }" @click="themeStore.update({ shadowLevel: option.value })">{{ option.label }}</button>
          </span>
        </label>
      </div>
    </section>

    <section class="panel">
      <h3>字体与密度</h3>
      <div class="field-row">
        <label class="field">
          <span class="field-label">全局字号：{{ settings.fontSize }}px</span>
          <input type="range" min="12" max="17" step="1" :value="settings.fontSize" @change="themeStore.update({ fontSize: Number(($event.target as HTMLInputElement).value) })" />
        </label>
        <label class="field">
          <span class="field-label">页面密度</span>
          <span class="seg">
            <button v-for="option in densityOptions" :key="option.value" type="button" :class="{ active: settings.density === option.value }" @click="themeStore.update({ density: option.value })">{{ option.label }}</button>
          </span>
        </label>
      </div>
    </section>

    <section class="panel">
      <h3>导航布局</h3>
      <div class="field-row">
        <label class="field">
          <span class="field-label">导航位置</span>
          <span class="seg">
            <button type="button" :class="{ active: settings.navLayout === 'sidebar' }" @click="themeStore.update({ navLayout: 'sidebar' })">左侧导航</button>
            <button type="button" :class="{ active: settings.navLayout === 'top' }" @click="themeStore.update({ navLayout: 'top' })">顶部导航</button>
          </span>
        </label>
        <label v-if="settings.navLayout === 'sidebar'" class="field">
          <span class="field-label">侧边栏宽度：{{ settings.sidebarWidth }}px</span>
          <input type="range" min="190" max="320" step="4" :value="settings.sidebarWidth" @change="themeStore.update({ sidebarWidth: Number(($event.target as HTMLInputElement).value) })" />
        </label>
        <label v-if="settings.navLayout === 'sidebar'" class="field">
          <span class="field-label">侧栏折叠</span>
          <span class="seg">
            <button type="button" :class="{ active: !settings.sidebarCollapsed }" @click="themeStore.update({ sidebarCollapsed: false })">展开</button>
            <button type="button" :class="{ active: settings.sidebarCollapsed }" @click="themeStore.update({ sidebarCollapsed: true })">折叠（仅图标）</button>
          </span>
        </label>
      </div>
    </section>

    <section class="panel">
      <h3>ECharts 图表色盘</h3>
      <div class="palette-grid">
        <button
          v-for="palette in CHART_PALETTES"
          :key="palette.id"
          type="button"
          class="palette-card"
          :class="{ active: settings.chartPaletteId === palette.id }"
          @click="themeStore.update({ chartPaletteId: palette.id })"
        >
          <span class="palette-name">{{ palette.name }}</span>
          <span class="palette-strip">
            <span
              v-for="(color, index) in palette.colors ?? themeStore.chartColors"
              :key="index"
              class="palette-swatch"
              :style="{ background: color }"
            ></span>
          </span>
        </button>
      </div>
      <p class="hint">切换色盘后所有图表即时重绘；「跟随主题主色」会取当前预设/自定义主色作为图表首色。</p>
    </section>

    <section class="panel">
      <h3>仪表盘组件显示与排列</h3>
      <ul class="widget-list">
        <li v-for="(widget, index) in widgetList" :key="widget.id">
          <label class="widget-visible">
            <input
              type="checkbox"
              :checked="!settings.dashboardHidden.includes(widget.id)"
              @change="themeStore.toggleDashboardWidget(widget.id)"
            />
            <span>{{ widget.name }}</span>
          </label>
          <span class="widget-order">
            <button class="mini" type="button" :disabled="index === 0" @click="moveWidget(widget.id, -1)"><PixelIcon name="arrow-up" :size="14" /></button>
            <button class="mini" type="button" :disabled="index === widgetList.length - 1" @click="moveWidget(widget.id, 1)"><PixelIcon name="arrow-down" :size="14" /></button>
          </span>
        </li>
      </ul>
      <p class="hint">仅作用于「市场总览」页；取消勾选即隐藏，↑↓ 调整渲染顺序。</p>
    </section>

    <section class="panel">
      <h3>主题预设导入 / 导出</h3>
      <div class="io-row">
        <button class="mini" type="button" @click="downloadExport">下载主题 JSON</button>
        <button class="mini" type="button" @click="copyExport">{{ exportCopied ? "已复制 ✓" : "复制到剪贴板" }}</button>
        <label class="mini import-file">
          选择文件导入
          <input type="file" accept="application/json,.json" @change="onImportFile" />
        </label>
      </div>
      <textarea
        v-model="importText"
        class="import-area"
        rows="5"
        placeholder='粘贴主题 JSON（形如 {"app":"aiquant-lite","kind":"wl-theme-settings","settings":{…}}）后点击导入'
      ></textarea>
      <div class="io-row">
        <button class="mini primary" type="button" @click="doImport">导入并应用</button>
        <button class="mini danger" type="button" @click="themeStore.resetAll(); importText = ''">恢复全部默认</button>
        <span v-if="importMessage" class="import-message" role="status">{{ importMessage }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.mode-seg { margin-bottom: 14px; }
.mode-seg button { padding: 8px 16px; }
.preset-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.preset-card { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-card); background: var(--bg-panel); cursor: pointer; text-align: left; transition: all 0.16s ease; }
.preset-card:hover { border-color: var(--accent-ring); }
.preset-card.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }
.preset-swatch { position: relative; width: 100%; height: 52px; border-radius: var(--radius-inner); border: 1px solid var(--border-soft); overflow: hidden; }
.preset-swatch-accent { position: absolute; left: 10px; top: 16px; width: 20px; height: 20px; border-radius: 6px; border: 2px solid rgba(255, 255, 255, 0.85); }
.preset-swatch-card { position: absolute; right: 12px; top: 10px; width: 56px; height: 32px; border-radius: 8px; border: 1px solid var(--border-soft); }
.preset-name { font-weight: 700; font-size: 13.5px; }
.preset-desc { font-size: 11.5px; color: var(--text-dim); line-height: 1.5; }
.field-row { display: flex; flex-wrap: wrap; gap: 22px; align-items: flex-end; }
.field { display: flex; flex-direction: column; gap: 7px; min-width: 200px; }
.field-label { font-size: 12.5px; color: var(--text-dim); }
.field-control { display: flex; align-items: center; gap: 8px; }
.field-control input[type="color"] { width: 38px; height: 32px; padding: 2px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-panel); cursor: pointer; }
.field-control input[type="text"] { width: 110px; }
input[type="range"] { accent-color: var(--accent); width: 220px; }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--text-faint); }
.palette-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.palette-card { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-inner); background: var(--bg-panel); cursor: pointer; }
.palette-card.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }
.palette-name { font-size: 12.5px; font-weight: 600; }
.palette-strip { display: flex; gap: 4px; }
.palette-swatch { width: 20px; height: 20px; border-radius: 5px; border: 1px solid var(--border-soft); }
.widget-list { list-style: none; margin: 0 0 6px; padding: 0; display: flex; flex-direction: column; gap: 6px; max-width: 460px; }
.widget-list li { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); background: var(--bg-panel-2); }
.widget-visible { display: flex; align-items: center; gap: 9px; font-size: 13px; cursor: pointer; }
.widget-visible input { accent-color: var(--accent); }
.widget-order { display: flex; gap: 6px; }
.io-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.import-file { position: relative; overflow: hidden; cursor: pointer; }
.import-file input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.import-area { width: 100%; min-height: 96px; resize: vertical; font-family: Consolas, monospace; font-size: 12px; color: var(--text); background: var(--control-bg); border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 10px 12px; margin-bottom: 10px; }
.import-message { font-size: 12px; color: var(--text-dim); }
</style>

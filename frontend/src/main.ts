import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import { applyTheme, loadSettings } from "./domain/theme";
import "./styles/tokens.css";
import "./style.css";

// 挂载前先落主题，避免首屏按默认深色渲染再闪烁
applyTheme(loadSettings());

createApp(App).use(createPinia()).use(router).mount("#app");

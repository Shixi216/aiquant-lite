# -*- coding: utf-8 -*-
"""弹窗关闭与排序箭头接入像素图标（一次性脚本）。"""

def patch(path, pairs, imports=None):
    src = open(path, encoding="utf-8").read()
    if imports:
        anchor, imp = imports
        if imp.strip().splitlines()[-1].strip() not in src.split("<script")[1].split("</script>")[0]:
            assert anchor in src, path + " import anchor"
            src = src.replace(anchor, anchor + "\n" + imp, 1)
    for old, new in pairs:
        if old in src:
            src = src.replace(old, new)
        else:
            print("MISS", path, old[:60])
    open(path, "w", encoding="utf-8", newline="\n").write(src)
    print("done", path)

ICON_IMPORT = (
    'import { errorMessage } from "../api/client";',
    'import PixelIcon from "../components/global/PixelIcon.vue";')

# Dashboard：排序箭头
patch("src/views/DashboardView.vue", [
    ('@click="moveWidget(id, -1)">↑</button>', '@click="moveWidget(id, -1)"><PixelIcon name="arrow-up" :size="14" /></button>'),
    ('@click="moveWidget(id, 1)">↓</button>', '@click="moveWidget(id, 1)"><PixelIcon name="arrow-down" :size="14" /></button>'),
], ICON_IMPORT)

# Settings：排序箭头
patch("src/views/SettingsView.vue", [
    ('@click="moveWidget(widget.id, -1)">↑</button>', '@click="moveWidget(widget.id, -1)"><PixelIcon name="arrow-up" :size="14" /></button>'),
    ('@click="moveWidget(widget.id, 1)">↓</button>', '@click="moveWidget(widget.id, 1)"><PixelIcon name="arrow-down" :size="14" /></button>'),
], ('import { computed, ref } from "vue";', 'import PixelIcon from "../components/global/PixelIcon.vue";'))

# TaskCenter：确认弹窗关闭
patch("src/views/TaskCenterView.vue", [
    ('aria-label="取消" @click="pendingConfirm = null">✕</button>',
     'aria-label="取消" @click="pendingConfirm = null"><PixelIcon name="close" :size="14" /></button>'),
], ICON_IMPORT)

# Sentiment：证据抽屉 + 确认弹窗关闭
patch("src/views/WealthSentimentView.vue", [
    ('aria-label="关闭" @click="evidenceFor = null">✕</button>',
     'aria-label="关闭" @click="evidenceFor = null"><PixelIcon name="close" :size="14" /></button>'),
    ('aria-label="取消" @click="confirmDialog = null">✕</button>',
     'aria-label="取消" @click="confirmDialog = null"><PixelIcon name="close" :size="14" /></button>'),
], ('import { fetchV3Quality } from "../api/wealthLabV3";', 'import PixelIcon from "../components/global/PixelIcon.vue";'))

# Decisions：版本链弹窗关闭
patch("src/views/DecisionsView.vue", [
    ('<button class="close-btn" @click="versionChain = null; versionError = null">✕</button>',
     '<button class="close-btn" aria-label="关闭" @click="versionChain = null; versionError = null"><PixelIcon name="close" :size="14" /></button>'),
], ('import PixelIcon from "../components/global/PixelIcon.vue";', 'import PixelIcon from "../components/global/PixelIcon.vue";'))

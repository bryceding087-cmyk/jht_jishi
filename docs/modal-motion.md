# 居中弹窗动效

`public/index.html` 全局加载 `modal-motion.js` 和 `modal-motion.css`。弹窗首次显示时从最近点击位置（键盘操作使用触发元素中心）缩放、移动至最终位置。动画持续 380ms，遮罩淡入 220ms。没有近期触发操作时从中心附近轻微放大。

## 已覆盖入口

- `app.js` 的 `showModal`：项目新建、重命名、移动、回收站、永久删除；最近对话与画布重命名和删除；模型用量提示；分镜编辑和资源选择。
- `asset-library.js` 借助 `showModal`：素材预览、编辑、移动、重命名、删除；文件夹创建、重命名和删除。
- `image-generation.js` 的 `.ig-dialog` 和 `audio-generation.js` 的 `.ag-dialog`。
- `agent-chat.js` 的原生 `dialog`：引用素材、创作记录、偏好、技能和文档查看。
- `home-composer.js` 的原生 `dialog`：生成详情。

## 新增弹窗默认约定

优先使用已有 `showModal` 或原生 `<dialog>` + `showModal()`，自动带动画。独立组件使用 `role="dialog" aria-modal="true"`，同样自动覆盖；其他自定义居中弹窗可添加 `data-centered-modal`。独立遮罩添加 `data-modal-backdrop` 可获得遮罩淡入，并在替换弹窗 DOM 时保留同一次入场时间线。

业务函数无需调用动画，也不要另加缩放或位移动画。动画结束后清理临时效果，保持原有布局、滚动和焦点行为。相同弹窗的内容刷新不重播；关闭后重开会重播。共用容器通过弹窗类名和 `h2` 标题区分不同弹窗，从素材详情切换到删除确认也会播放入场。系统启用“减少动态效果”时直接显示。

靠近按钮显示的菜单、popover、画布侧栏和浮动编辑面板不属于居中模态弹窗，保留自身定位和动效语义。

# Plugin API 技术细节

写 Figma 脚本前读。具体能力与参数以当前工具文档和 figma-use 为准，两者冲突时以它们为准。本文件只记长期有效的行为，不记项目 ID 与报错流水。工具分工另见 [工具与流程](figma-workflow-tools.md)。

## 脚本与原子性

- 脚本仅在**未捕获**异常时整体回滚。对每项加 try/catch 后，捕获前的修改会保留，留下半迁移节点。多步修改不要吞异常继续，除非恢复路径明确。
- 可选覆盖先判断再设，如 `if (x !== undefined)`；把 `characters` 设为 undefined 会抛错。
- 每次写入返回创建／修改的节点 ID，长任务把导入的 key、样式 ID、节点 ID 和未决缺口记到任务记录。
- 写入失败或超时可能已部分完成，先从稳定根查，再补缺。
- 编辑或切换实例内被隐藏的层时，脚本顶部设 `figma.skipInvisibleInstanceChildren = false`，否则 `findOne/findAll/children` 会跳过 `visible=false` 的层，表现为查不到。

## 字体

- 本机 Console 路径可用本机字体；官方 MCP `use_figma` 运行在云端，仅能加载 Google Fonts，无法加载苹方。判断用哪条路径写字前先确认。
- 云端回退写法：先把节点 `fontName` 设为可加载字体 → 写 `characters` → 最后 `await node.setTextStyleIdAsync(styleId)`。应用文本样式不需要加载其字体，用户机器上将显示为真实字体。顺序反了，节点会再次变成不可加载字体而写入失败。
- 只需加载**新** `fontName`，卡在不可加载字体上的节点可先换字体、编辑，再恢复样式。
- 套样式前，先给回退字体设好样式的 `fontSize` 和 `lineHeight`，否则节点可能保留旧度量而渲染偏小。
- 最后统一把剩余回退字体片段换回库文本样式，终稿不留回退字体。
- 组件的 TEXT 属性在字体不可用时无法用 `setProperties` 设置，改为直接改实例内文本节点（fontName → characters → 样式）。
- 固定宽按钮内的文字：按钮固定宽，文字 FILL 宽并居中；Hug 宽文字用回退字体测宽，换真实字体后会偏。
- 主组件内改 `textAutoResize` 后，需对各实例受影响文本重设 characters，否则宽度滞后而溢出。
- `swapComponent` 保留的文本覆盖不需要再加载字体。

## 布局

- `resize(w, h)` 会把自动布局尺寸模式重置为 FIXED，之后重新设 `primaryAxisSizingMode` 与 `layoutSizing*`，否则节点塌缩。
- `layoutSizingHorizontal/Vertical` 的 FILL／HUG 要在 `appendChild` 进自动布局父级**之后**设。
- 文本节点没有内边距：外包自动布局框，给框设内边距。
- `figma.createFrame()` 默认 `clipsContent = true`；无滚动、遮罩等理由时设为 `false`。
- 头部或底部描边设 `strokesIncludedInLayout = false`，避免 1px 描边把 56 的栏撑成 57。
- 自动布局流内的 1px 强调线夹在有边框的行之间会显得约 2px：改为 `layoutPositioning = 'ABSOLUTE'`，压在共享边框上（`y = 行底 - 0.5`）。
- 手搭表格插列时，要同步右移插入点右侧所有绝对定位子层（说明、覆盖矩形），改前改后各截图。

## 节点、克隆与 Section

- Section 内框架 `clone()` 可能落到页面根：始终 `section.appendChild(clone)`，重设相对 Section 的 x/y，并读 `clone.parent.id` 确认。
- Section 截图只含它的**子节点**，需要时先重新归入 Section。
- Section 没有自动适应尺寸：自行算子节点包围盒再设位置和尺寸。
- 新克隆框架上 `findOne` 对实例内部可能返回 null，直接遍历 `.children`；节点名重复时从具体容器内查找。
- 把文本层移入组件内新框架会丢失其 `componentPropertyReferences`：重新绑定，并核实例仍显示各自覆盖的文本。
- 不要把带布局特定覆盖的实例（如仅上下描边、圆角 0 的通栏提示）克隆到别的场景，改为新建该变体的实例。
- 未发布组件不能按 key 导入，可取现有实例的主组件或克隆现有实例。

## 变量与绑定

- 绑定到渐变停靠点的变量会把该停靠点 alpha 强制为 1；绑定的描边、效果颜色同样强制 alpha 1，所以不能用它们做柔和光圈。
- 变量绑定的填充上，paint 级 `opacity` 在主组件里正常，在实例里会显示为实心（API 仍读到原值）。需要浅色底时，用子层加图层 `opacity`；渐变则绑到「主色 → 白」并用 paint opacity 控制强度，底下垫白色变量实心。
- 实例上只绑定被覆盖的字段（看 `instance.overrides`）；重绑未覆盖的子层会产生新覆盖。
- 第三方品牌色（如支付卡标）无 token 时保留原色，批量重绑时排除，并在交付中说明。

## 截图与验收

- 最终截图使用 Console 本机截图；`node.screenshot()` 在云端无法渲染真实中文字体，可能掩盖布局问题。
- 部分云端渲染对个别实例（如表格单元格）仍会空白：如实说明，请用户在 Figma 中确认，不据此宣称完成。
- 图片填充 hash 非空不证明可见，核真实填充、比例与渲染。

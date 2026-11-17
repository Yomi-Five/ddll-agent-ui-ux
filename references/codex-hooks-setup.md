# 与 GPT 和 Codex Hooks 配合

用于安装、配置和排查本技能的自动恢复。这里的 Hook 是 **Codex 宿主的生命周期 Hook**：宿主执行本地 Python 脚本，把技能入口作为额外上下文交给当前 GPT 模型；不是给普通 ChatGPT 网页聊天添加本地脚本，也不是 OpenAI API Webhook。技能负责设计规则，Hook 负责在恢复和新一轮输入时送达规则，两者不互相替代。

配置结构、事件和信任机制以 [Codex 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks) 为准。本说明对应仓库内的 [design_resume.py](../scripts/design_resume.py)；设计任务中的登记规则见[设计会话自动恢复](session-recovery.md)。

## 1. 准备运行环境

- 使用支持这些 Hook 事件的 Codex 客户端。桌面端在「设置 → Hooks」检查；CLI 用 `/hooks` 检查，入口以当前客户端为准。
- 完整安装技能，保留 `scripts/` 与 `references/`。软链接安装可用，脚本会按真实文件位置读取相邻 `SKILL.md`。
- 使用可实际运行的 Python 3，并取得解释器和 `scripts/design_resume.py` 的绝对路径。脚本仅依赖 Python 标准库，使用 `fcntl` 文件锁，适用于 macOS／Linux；Windows 原生环境需先适配，不直接套用。
- 首次执行仍需显式调用技能或通过全局指令加载，再登记设计聊天。Hook 不按用户输入里的关键词猜测新任务；`PostToolUse` 在工具执行后才触发，不能代替第一次编辑前读取入口。

## 2. 合并三个 Hook

打开 [codex-hooks.example.json](../examples/codex-hooks.example.json)，将命令里的两处占位路径替换为本机实际的 Python 和脚本绝对路径，保留路径外的引号。三个处理器调用同一个脚本，**不带** `activate` 等子命令；脚本从标准输入接收宿主事件 JSON。

将模板中的事件组追加到当前 Codex 配置目录的 `hooks.json`（默认 `~/.codex/hooks.json`）。使用自定义 `CODEX_HOME` 时沿当前生效配置目录；不要直接覆盖已有文件或移除其他处理器。项目内 `.codex/hooks.json` 也可配置，但其配置层须可信；避免同一套处理器在用户级、项目级或内联 `[hooks]` 中重复安装，因为匹配的处理器会一起运行。

| 事件 | 本技能的作用 |
|---|---|
| `SessionStart` | 在启动、恢复、压缩、分叉时为已登记的当前聊天加载入口；`clear` 清除当前聊天的活动状态 |
| `UserPromptSubmit` | 当前聊天处于设计活动状态时，在每轮用户输入前加载入口，包括只输入「继续」的情况 |
| `PostToolUse` | 实际 Figma／Console 工具调用后自动登记当前聊天，作为手动登记的补充 |

保留 `timeout: 5` 和 `additionalContextLimit: 5000`。后者是额外上下文的近似 token 阈值，不是预留 token 或增加的固定消耗；入口超过阈值时宿主可能仅送达预览与落盘路径。只有最新入口原文完整送达才视为已读，截断时仍需读取完整文件。引用仍按本轮路由加载，不把整个技能目录注入上下文。

示例只包含可移植的处理器结构，不包含任何人的信任记录、聊天状态、任务记录或凭据。

## 3. 审核并信任

配置后在客户端 Hook 管理界面逐项检查三个事件的命令、路径和来源，并由用户审核信任。Codex 会跳过尚未信任或定义发生变化的非托管处理器；安装技能、启用插件或写好 JSON 都不等于已经信任。命令或配置定义变化后按客户端提示重新审核，不复制其他机器的信任哈希。

如果当前宿主不提供相应事件或处理器被禁用，使用下面的全局指令与手动登记流程，不声称自动恢复已启用。

## 4. 配合全局指令和任务记录

将以下内容合并到 Codex 用户级 `AGENTS.md`（默认 `~/.codex/AGENTS.md`），替换技能入口的占位绝对路径，保留已有其他规则：

```text
每轮涉及设计、新建或修改设计内容（含改字、内容替换、布局、组件、图标、插画及动效）、纠错、续做或设计评审时，执行前必须读取 /absolute/path/to/ddll-agent-ui-ux/SKILL.md，并按入口路由读取适用章节。同轮连续操作复用仍有效的引用原文；后续新一轮修改重新读取入口。上下文恢复、规则更新或范围变化按入口要求补读。
可信 Hook 已完整注入最新入口时视为本轮已读，不重复加载。恢复时先核当前聊天的任务标记和原任务记录；用户只说“继续”且历史／记录表明是设计任务时仍须加载，不因缺少设计关键词跳过。设计任务首次开始、完成、取消或切换无关任务时，按技能的恢复登记规则更新当前聊天状态。
```

首次开始设计／只读评审时，由执行该任务的 agent 关联已有任务记录。以下为手动命令示例，路径须替换为实际值：

```bash
DESIGN_PYTHON="/absolute/path/to/python3"
DESIGN_SKILL_DIR="/absolute/path/to/ddll-agent-ui-ux"
"$DESIGN_PYTHON" "$DESIGN_SKILL_DIR/scripts/design_resume.py" activate \
  --record "/absolute/path/to/current-task-record.md"
"$DESIGN_PYTHON" "$DESIGN_SKILL_DIR/scripts/design_resume.py" status
```

`--record` 必须指向真实存在的当前任务记录；复用原记录，不另建一套业务台账。命令默认从当前任务的 `CODEX_THREAD_ID` 取聊天 ID；外部终端没有该环境变量时，使用该聊天 Hook 提示中的真实 ID，显式追加 `--session-id`，不要拿目录、标题或随机值代替。上面的 shell 变量仅方便手动运行；Hook 配置中的命令仍填写实际绝对路径，不依赖这个终端的临时变量。

每批进度和下一步写回原任务记录。全部适用项完成并验收后运行：

```bash
"$DESIGN_PYTHON" "$DESIGN_SKILL_DIR/scripts/design_resume.py" complete
```

取消或切换无关任务时运行：

```bash
"$DESIGN_PYTHON" "$DESIGN_SKILL_DIR/scripts/design_resume.py" deactivate
```

这是任务状态，由 agent 按实际完成情况更新；不要把关闭窗口、`SessionEnd` 或每轮回答结束自动等同于设计任务完成。需要接续时重新 `activate` 并核原记录。新聊天／分叉不按目录继承另一聊天的活动状态，须关联其实际原任务记录。

## 5. 验证配置与恢复

先运行仓库已有回归测试：

```bash
"$DESIGN_PYTHON" "$DESIGN_SKILL_DIR/scripts/test_design_resume.py"
```

这覆盖恢复、压缩、仅输入「继续」、同轮去重、入口变更、聊天隔离、旧聊天补登及结束／取消等分支，测试使用临时状态目录。通过后还要核宿主实际事件：

1. 在当前设计聊天登记真实任务记录，检查 `status` 为 `active` 且路径正确。
2. 恢复该聊天，或在支持的客户端执行压缩；核 Hook 实际运行并完整送达最新入口，而非仅有命令执行成功或截断预览。
3. 下一轮只输入「继续」，核执行前沿原任务记录接续；不依赖输入里的设计关键词。
4. 核另一无关聊天没有被继承登记；完成／取消后再次输入无关内容，不再自动注入设计入口。

同轮 `turn_id` 重复事件会去重；启动／恢复后紧随的首条输入复用该次送达，后续新轮重新加载。入口内容的指纹变化使旧缓存失效，压缩后重新送达原文；引用文件仍依技能路由和有效上下文补读。

## 常见问题

| 现象 | 核查与处理 |
|---|---|
| JSON 已写但 Hook 不运行 | 查当前配置层、是否启用、三个处理器是否已审核信任，以及命令路径和 Python 是否可运行 |
| 输入「继续」没有加载 | 查该聊天 `status` 及记录路径；未登记的新任务先显式启用技能并 `activate`，不要只查关键词 |
| 旧聊天未自动补登 | 历史补登仅尽力读取当前聊天记录末尾最多 8 MiB 的实际 Figma 调用；记录格式不是稳定接口，失败时按提示手动读取入口并关联原记录 |
| 同轮重复加载 | 查是否在多个配置层重复安装；确认宿主提供 `turn_id`，保留脚本自带去重逻辑 |
| 自动加载报错或仅收到预览 | 查处理器输出；手动完整读取入口并核当前任务记录，不把失败或截断当作已加载 |
| 普通任务仍注入设计入口 | 核上个任务是否忘记 `complete`／`deactivate`；状态按聊天保存，不用扫描或清空其他聊天解决 |

状态保存在当前 Codex home 的 `design-skill-state/`，按聊天 ID 隔离，仅记录状态、入口指纹和原任务记录路径等恢复元数据；这些本机文件不提交到本技能仓库。Hook 不授予 Figma 编辑、库发布或消息发送权限，恢复后仍遵守原范围与只读约束。

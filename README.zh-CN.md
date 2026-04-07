# WRF Skill 部署说明

这个仓库不是写给 WRF 内部开发者看的，而是写给正在用 Claude Code 或 Codex 的用户。
它的作用不是“安装一个完整的 WRF/WPS 发行版”，而是把一套可复用的 WRF 工作流交给 AI：初始化项目、生成配置、跑 WPS/WRF、管理可选的 HPC 提交。

[Back to English README](README.md)

## 这里说的“部署”到底是什么意思

这个仓库本质上是一个工作流层，不是 WRF/WPS 安装器。
所以“部署”指的是把这套工作流放到 AI 能看到、能调用的工作区里。

- Claude Code：把这个仓库，或它的 bundle 安装结果，放进一个包含 `.claude/skills/` 的工作区。
- Codex：把这个仓库，或它的 bundle 安装结果，放进 Codex 能直接操作的工作区。
- 两边都一样的一点：WRF/WPS 二进制、地理数据、表文件、集群访问能力，仍然要你自己提供。

如果你只记一件事，就记这个：

- Claude Code 的部署重点是 `.claude/skills/`
- Codex 的部署重点是“让它能看到这套脚本、模板、配置和 `runs/` 状态”
- 这个仓库不负责替你安装 WRF/WPS 本体

## 部署前你要先准备什么

至少先准备好这些：

- Linux 或 WSL 环境
- Python 3.10+
- 已经编译好的 WRF 和 WPS
- 必要的 `WPS_GEOG` 数据和运行支持文件
- 你的资料源访问方式
- 如果要跑 HPC：调度器访问方式、登录节点路径、站点自己的运行约束

这个仓库目前明确不做这些事：

- 自动编译 WRF/WPS
- 自动接入任意新资料源
- 自动识别你集群里没暴露出来的策略和配置
- 放开任意本地 shell 命令链给 AI 执行

## 怎么部署到 Claude Code

Claude Code 是最直接的目标，因为仓库已经自带了 `.claude/skills/`。

### 方式一：直接用仓库

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

如果你要跑 HPC，先复制一份本地环境配置：

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

然后在 Claude Code 里打开这个工作区即可。Claude Code 会从 `.claude/skills/` 里发现这些 skill。

### 方式二：打包成 bundle，再装进另一个工作区

如果你要发给别人，或者想把工作区做得更干净，用 bundle：

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
```

解压后安装到目标工作区：

```bash
tar -xzf dist/wrf-skill-bundle.tar.gz
cd wrf-skill-bundle
python3 scripts/install_skill_bundle.py --target /path/to/claude-workspace
```

如果目标目录已经有同名 bundle 文件，需要你显式加 `--force`。

## 怎么部署到 Codex

这个仓库现在已经带了一套 repo-local 的原生 Codex plugin，位置在 `plugins/wrf-skill/`。
它本质上是一层很薄的封装，下面仍然调用仓库里的工作流脚本，而 `.agents/plugins/marketplace.json` 会把它暴露成当前工作区里的本地 plugin。

### 方式一：直接用仓库

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

然后直接在 Codex 里打开这个仓库即可。本地 marketplace 已经指向 `plugins/wrf-skill/`。

### 方式二：把 bundle 安装进一个干净工作区

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
tar -xzf dist/wrf-skill-bundle.tar.gz
cd wrf-skill-bundle
python3 scripts/install_skill_bundle.py --target /path/to/codex-workspace
```

之后在 Codex 里打开目标工作区即可。现在 bundle 里已经会包含原生 plugin 文件和工作流脚本，所以它既能按 plugin 方式发现，也能直接操作工作区状态。

这个 plugin 的边界也要讲清楚，它默认假设同一个工作区里还存在：

- `scripts/`
- `config/`
- `templates/`
- `runs/`

### 方式三：全局注册 plugin，并顺手部署好 Codex 工作区

```bash
bash scripts/install_codex_plugin.sh
```

默认会一次性做完这几件事：

- 把 plugin 安装到 `~/plugins/wrf-skill`
- 更新 `~/.agents/plugins/marketplace.json`
- 把兼容工作区部署到 `~/codex-workspaces/wrf-skill-workspace`
- 输出一段 `AI handoff`，明确告诉你工作区已经就绪，以及路径在哪里

如果你要改默认位置，可以这样：

```bash
bash scripts/install_codex_plugin.sh \
  --plugins-dir /path/to/plugins \
  --marketplace-path /path/to/marketplace.json \
  --workspace-root /path/to/wrf-skill-workspace
```

如果你只想全局注册 plugin，不想顺手复制工作区，可以这样：

```bash
bash scripts/install_codex_plugin.sh --no-workspace
```

只要脚本部署了工作区，后面真正跑 WRF 时，就直接在 Codex 里打开那个部署好的路径。

实际可用的说法类似：

- `用 scripts/wrf_init.py 初始化一个 demo 项目。`
- `用 scripts/wrf_config.py 把这个请求渲染成一个本地 WPS 案例。`
- `只跑 runs/<project> 的 WPS 流程。`

## bundle 里到底有什么

bundle 的作用是“把工作流层干净地带走”，不是把你的整个运行环境打包出去。

它会包含：

- `.agents/plugins/marketplace.json`
- `.claude/skills/`
- `plugins/wrf-skill/`
- `scripts/`
- `templates/`
- 必要的 schema 和 preset 配置
- `third_party/wps-support/` 里的轻量支持文件

它故意不包含：

- `config/wrf_env.json`
- `runs/` 里的实际输出
- 编译好的 WRF/WPS 目录
- 完整 `WPS_GEOG`
- 私有 SSH / 调度器凭证

## 部署后最少还要配什么

### 本地运行

直接接你现有的本地 WRF/WPS 环境即可。
仓库已经支持本地 runtime 定制，但边界是收紧的。

关键限制：

- 本地自定义只支持 `custom_safe`
- `custom_safe` 只接受结构化 argv 模板
- 不允许原始 shell 字符串
- 不允许 `bash -lc`、管道、重定向、`source`、`&&` 这类命令链

这不是能力不够，而是安全边界故意这么收。

### HPC 运行

先从示例配置复制一份：

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

然后把你站点自己的信息填进去，例如：

- 调度器类型
- 登录节点接入方式
- 远端运行目录
- 可执行文件路径
- queue / account 默认值
- 站点自己的路径规范

这些东西仓库无法替你猜。

## AI 部署后到底能看到什么

这部分最好在 README 里直接讲透，不然后面用户会误解。

部署完成后，AI 只能看到“当前工作区里存在的文件”和“当前会话里真的能调用到的命令”。

通常它可以：

- 读取 `runs/<project>/project.json`
- 读取你提供的 `config/wrf_env.json`
- 查看已有日志
- 调用当前环境已经可用的本地命令或 HPC 接入路径

它通常不能自动做到：

- 看见你没有暴露出来的集群内部配置
- 知道实时可用资源，除非环境里本来就有相应命令或接口可查
- 替你补装缺失的 WRF/WPS 依赖
- 把任意 shell 片段安全地变成运行配置

所以如果你问“现在的 skill 能不能看到 HPC 的配置和实时资源”，更准确的答案是：

- 能看到你放进工作区、或当前环境能访问到的配置
- 能查询当前会话已经暴露出来的资源信息
- 不能凭空发现隐藏信息

## 部署后的首个可用流程

最小可用的本地流程如下：

```bash
python3 scripts/wrf_init.py --project-name demo
python3 scripts/wrf_config.py \
  --project-name demo \
  --request-text "East China, GFS, 2024-07-20 00:00 to 2024-07-20 12:00, local" \
  --run-mode local
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

如果你这次只想验证预处理链路，跑到 `wrf-wps` 就可以停。

## 这个仓库现在最适合怎么用

把它理解成下面三件事之一，基本就不会用偏：

- Claude Code 的 WRF skill 工作区
- 原生 Codex plugin 加 WRF workflow 工作区
- 可以发给别人的 bundle 化工作流层

不要把它理解成：

- 自带 WRF/WPS 编译产物的安装包
- 任意 shell 自动化执行器
- 能自动读懂所有 HPC 站点隐式规则的智能代理

## 第三方文件和许可证

轻量 WPS 支持文件见 [THIRD_PARTY.md](THIRD_PARTY.md)。
项目自行编写的文件按 [Apache-2.0](LICENSE) 发布。

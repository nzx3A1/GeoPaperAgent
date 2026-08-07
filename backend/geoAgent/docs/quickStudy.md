它的核心思想是：

> **Agent 只负责循环控制，模型负责决定下一步 Bash 操作，Environment 负责执行命令。**


# 一、总体架构

```text
用户任务
   ↓
CLI / Run入口
   ↓
读取YAML配置
   ↓
DefaultAgent
   ├── Model
   │     └── 调用大模型，生成Bash动作
   │
   ├── Environment
   │     └── 在本地、Docker或其他沙箱执行命令
   │
   ├── Message History
   │     └── 保存任务、模型回复、命令结果
   │
   └── Agent Loop
         └── 查询模型 → 执行动作 → 返回观察 → 再次查询
```

最简单的启动方式就是组合三个对象：

```text
DefaultAgent(
    LitellmModel(...),
    LocalEnvironment(...)
)
```

然后调用：

```text
agent.run(task)
```

官方最小启动文件也是这样组装 Agent、模型和本地环境的。

---

# 二、四个核心模块

## 1. Agent：循环控制中心

核心文件：

```text
src/minisweagent/agents/default.py
```

`DefaultAgent` 不负责具体代码分析，也不直接执行 Bash，它主要维护：

* 消息历史 `messages`
* 模型实例 `model`
* 执行环境 `env`
* 模型调用次数
* 已消耗费用
* 连续格式错误次数
* 开始时间
* 输出轨迹路径

这些状态都集中在 Agent 对象中。

它最核心的执行逻辑可以简化为：

```text
run()
  ├── 初始化system和user消息
  ├── while True
  │     ├── step()
  │     │    ├── query()
  │     │    └── execute_actions()
  │     ├── 保存执行轨迹
  │     └── 检查是否退出
  └── 返回最终结果
```

源码中的关键逻辑是：

```text
step()
→ query()
→ execute_actions()
```

`query()` 调用模型，`execute_actions()` 把模型产生的动作交给环境执行。

因此，Agent Loop 本质上是：

```text
模型思考并产生动作
→ Environment执行动作
→ 将执行结果加入消息历史
→ 模型看到新的执行结果
→ 决定下一步动作
```

---

## 2. Model：模型适配与动作解析

核心文件：

```text
src/minisweagent/models/litellm_model.py
```

`LitellmModel` 负责：

* 接收 Agent 的消息历史
* 调用 LiteLLM
* 适配不同模型提供商
* 向模型声明 Bash 动作
* 解析模型返回的动作
* 统计调用成本
* 处理重试
* 将命令执行结果格式化为模型可读消息

当前实现通过 LiteLLM 调用模型，并向模型提供唯一的 Bash 工具。

模型返回后，会被转换成类似：

```json
{
  "role": "assistant",
  "content": "...",
  "extra": {
    "actions": [
      {
        "command": "find . -name '*.py'"
      }
    ],
    "cost": 0.01,
    "timestamp": 123456789
  }
}
```

其中真正交给环境执行的是：

```text
message.extra.actions
```

动作解析、成本和原始模型响应都会被保存在消息中。

### 模型层的价值

Agent 不直接依赖某家模型API，而是依赖统一的 `Model` 接口。

因此可以替换为：

```text
Claude
GPT
Gemini
DeepSeek
Qwen
本地Ollama模型
自建OpenAI兼容接口
```

README 说明项目通过 LiteLLM、OpenRouter、Portkey 等方式兼容多类模型服务。

---

## 3. Environment：命令执行环境

本地环境核心文件：

```text
src/minisweagent/environments/local.py
```

`LocalEnvironment` 负责：

* 接收 Bash 命令
* 设置工作目录
* 设置环境变量
* 设置超时时间
* 执行命令
* 捕获标准输出和错误
* 返回退出码
* 超时后终止进程
* 判断任务是否完成

环境配置很简单：

```text
cwd       工作目录
env       附加环境变量
timeout   单次命令超时时间
```

命令执行后，Environment 返回：

```json
{
  "output": "命令输出",
  "returncode": 0,
  "exception_info": ""
}
```

如果失败：

```json
{
  "output": "部分输出",
  "returncode": -1,
  "exception_info": "错误信息"
}
```

### 无状态Shell

mini-swe-agent 的一个重要设计是：

> 每个 Bash 动作都通过独立的子进程执行，不维持长期运行的 Shell 会话。

因此前一步执行：

```bash
cd /project
```

不会影响下一步命令。

下一步必须写成：

```bash
cd /project && python analysis.py
```

默认配置也明确说明，每次动作都会运行在新的子 Shell 中。

这样设计的优点是：

* 环境状态简单
* 任务容易复现
* 适合放入Docker沙箱
* 单次命令容易设置超时
* 不容易留下失控的后台进程
* 便于批量运行和评测

项目还支持把 LocalEnvironment 替换为 Docker、Podman、Singularity、Bubblewrap、Contree 等环境。

---

## 4. Runner和配置：组装系统

启动入口负责：

* 获取用户任务
* 获取模型名称
* 加载YAML配置
* 创建Model
* 创建Environment
* 创建Agent
* 启动Agent Loop

项目通过 `pyproject.toml` 注册命令行入口：

```text
mini
mini-swe-agent
mini-extra
```

其中 `mini` 和 `mini-swe-agent` 都指向主CLI应用。

默认配置位于：

```text
src/minisweagent/config/default.yaml
```

配置主要分成三部分：

```yaml
agent:
  system_template: ...
  instance_template: ...
  step_limit: ...
  cost_limit: ...

environment:
  env: ...

model:
  observation_template: ...
  model_kwargs: ...
  format_error_template: ...
```

也就是：

```text
Agent配置
模型配置
环境配置
```

---

# 三、完整执行流程

假设用户输入：

```text
检查项目中的代码错误并修复。
```

## 第一步：初始化消息

Agent根据模板生成：

```text
System Message
告诉模型可以操作计算机，并规定动作格式

User Message
包含用户的具体任务和推荐工作流程
```

系统模板和任务模板使用 Jinja2，可以注入：

* 用户任务
* 操作系统
* 工作目录
* 模型名称
* 已消耗费用
* 当前调用次数
* 运行时间

Agent通过模型、环境和自身状态合并这些模板变量。

## 第二步：模型生成动作

模型可能决定：

```bash
find . -maxdepth 2 -type f
```

## 第三步：环境执行动作

LocalEnvironment使用子进程执行命令，返回：

```text
returncode：0
output：
./README.md
./src/main.py
./tests/test_main.py
```

## 第四步：结果写回历史

执行结果被格式化成工具观察消息：

```text
<returncode>0</returncode>
<output>
./README.md
./src/main.py
./tests/test_main.py
</output>
```

默认配置还会对超过一万字符的输出进行截断，只保留头部和尾部，避免上下文被一次超长命令污染。

## 第五步：进入下一轮

模型看到刚才的目录结果，决定：

```bash
sed -n '1,200p' src/main.py
```

之后继续：

```text
查看文件
→ 找到问题
→ 编辑文件
→ 运行测试
→ 检查结果
```

## 第六步：任务结束

模型执行：

```bash
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
```

Environment检测输出第一行是否为这个特殊标记。

检测成功后抛出 `Submitted` 事件，Agent退出循环并返回最终结果。

---

# 四、消息历史与记忆方式

mini-swe-agent 没有复杂记忆系统，它使用的是**完全线性的消息历史**：

```text
System
User
Assistant Action 1
Tool Observation 1
Assistant Action 2
Tool Observation 2
Assistant Action 3
Tool Observation 3
...
Exit
```

每轮只是在 `messages` 后面追加新消息。

这种设计的优点：

* 容易理解
* 容易调试
* 可以完整回放
* 适合训练和轨迹分析
* 模型看到的上下文就是执行轨迹

缺点：

* 长任务会导致上下文越来越长
* 没有项目长期记忆
* 没有自动摘要和上下文压缩
* 没有事实、假设和结论的分类存储

这些正是你的 GeoAgent 后续需要扩展的地方。

---

# 五、执行限制与安全控制

`AgentConfig` 支持：

* `step_limit`：最大执行步数
* `cost_limit`：最大模型费用
* `wall_time_limit_seconds`：总运行时间限制
* `max_consecutive_format_errors`：连续格式错误上限
* `output_path`：轨迹保存路径

每次查询模型前都会检查：

```text
是否超过步骤限制
是否超过成本限制
是否超过总运行时间
```

每一轮结束后都会保存轨迹，即使发生格式错误或异常，也尽量保留执行历史。

---

# 六、执行轨迹结构

Agent可以把整个任务保存成JSON：

```json
{
  "info": {
    "model_stats": {
      "instance_cost": 0.23,
      "api_calls": 15
    },
    "config": {},
    "exit_status": "Submitted",
    "submission": "任务完成"
  },
  "messages": [],
  "trajectory_format": "mini-swe-agent-1.1"
}
```

轨迹中还会合并：

* Agent配置
* Model配置
* Environment配置
* 模型调用次数
* 成本
* 所有消息
* 最终输出
* 退出原因

这个机制适合你的 GeoAgent 用来保存：

* 调用了哪些地质Skill
* 执行了哪些Bash命令
* 修改了哪些文件
* 查询了哪些图谱节点
* 生成了哪些分析结果
* 为什么重新规划
* 最终报告路径

---

# 七、项目架构的核心设计原则

## 1. Agent极简

Agent只负责：

```text
调用模型
执行动作
保存观察
判断退出
```

它不内置复杂业务逻辑。

## 2. Bash作为统一工具接口

项目没有为文件读取、编辑、搜索、测试分别设计大量工具，而是统一通过Bash完成：

```text
读取文件  → cat、sed
搜索文件  → find、grep
编辑文件  → sed、cat、python
运行程序  → python、pytest
操作Git   → git
```

README明确说明，项目从Agent视角只提供Bash这一类操作能力。

## 3. Model、Agent、Environment解耦

可以分别替换：

```text
不同模型
不同Agent控制逻辑
不同执行环境
```

例如：

```text
DefaultAgent + Claude + LocalEnvironment
DefaultAgent + Qwen + DockerEnvironment
GeoAgent + DeepSeek + GeologicalSandbox
```

## 4. 配置驱动

系统提示词、任务模板、观察格式、限制条件都放在YAML中，而不是全部写死在代码里。

## 5. 轨迹完全可观测

所有模型回复、命令和执行结果都会保存，便于：

* 调试
* 回放
* 评测
* 成本分析
* 失败定位

---

# 八、用于GeoAgent时如何改造

建议保留 mini-swe-agent 的核心架构：

```text
GeoAgent
├── Agent Loop
├── Model
├── Environment
├── Messages
└── Trajectory
```

在此基础上逐步增加四部分。

## 第一阶段：保留Bash核心

```text
DefaultAgent
→ 改成GeoAgent

LocalEnvironment
→ 限制到地质项目目录

default.yaml
→ 改成地质任务系统提示词
```

第一版仍然只提供 Bash，让模型通过命令：

* 查找文献
* 读取Markdown
* 运行Python
* 分析CSV
* 编辑报告
* 验证输出文件

## 第二阶段：加入Skill Loader

在每轮模型调用前，根据当前任务加载相关 Skill：

```text
文献分析任务
→ geology-document-analysis

数据统计任务
→ geological-data-analysis

报告生成任务
→ geology-report-generation
```

Skill本质上可以先作为附加Prompt和工作流程，不必立即改造成复杂工具系统。

## 第三阶段：加入项目记忆

在原来的线性消息历史之外增加：

```text
project_memory.json
├── 项目目标
├── 区域和层位
├── 已确认结论
├── 用户修正
├── 研究假设
├── 待办事项
└── 成果文件
```

每次构造模型上下文时，只加载与当前任务相关的记忆。

## 第四阶段：加入地质工具

保留 Bash 作为通用能力，再提供结构化专业工具：

```text
query_knowledge_graph
read_las
analyze_well_log
parse_geological_figure
compare_stratigraphy
validate_geological_result
```

推荐采用：

> **Bash兜底 + 专业工具增强**

而不是完全删除 Bash。

---

# 九、最值得你学习的部分

对于 GeoAgent，最值得直接复用的是：

1. `DefaultAgent.run()`：循环执行框架。
2. `DefaultAgent.step()`：模型决策与环境执行分离。
3. `LocalEnvironment`：本地文件和Bash操作。
4. YAML模板：定义地质Agent行为。
5. 轨迹保存：记录完整执行过程。
6. 步骤、成本和时间限制：防止Agent无限循环。
7. 完成标记：明确任务结束协议。
8. Environment抽象：后续可替换为Docker地质沙箱。

它当前缺少、但你的 GeoAgent 需要补充的是：

```text
Skill动态加载
结构化任务清单
上下文压缩
项目长期记忆
地质专业工具
知识图谱查询
证据审查
危险命令审批
文件修改Diff
人工确认节点
```

因此最合理的路线不是直接大幅修改项目，而是：

```text
先复用Agent + Model + Environment三层架构
→ 替换地质任务Prompt
→ 限制本地工作区
→ 增加Skill Loader
→ 增加项目记忆
→ 最后增加专业地质工具
```

总体上，`mini-swe-agent` 可以看成一个非常干净的 **Agent Harness 内核**，而你的 GeoAgent 是在这个内核上增加地质领域能力、项目记忆和专业工具。

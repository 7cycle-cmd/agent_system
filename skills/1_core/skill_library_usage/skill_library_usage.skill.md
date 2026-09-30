---
task_id: SKILL.LIBRARY.USAGE
display_task_id: SKILL.LIBRARY.USAGE
name: skill_library_usage
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Worker guide for friendly use of the central Skill Library (search, reuse, orchestrate, sync)
reason: "Teach workers how to operate the Skill Library panel: search skills, read details, orchestrate into DB tasks, sync from files, and respect the skill/prompt division of labor"
artifacts:
  - skill_library_usage.skill.md
schema: result_yes_no
---
# Skill: skill_library_usage

Package path: `skills/1_core/skill_library_usage/`

## Prompt

你而家操作【Skill Library 面板】，规则同使用方法如下：

1. 面板结构
- 左边：Catalog分类目录（core / db_schema / ui / agent / qa），每一个catalog下面有subcatalog，存放唔同skill。
- 总数：17个skill，分布喺6个catalog。
- 右边：选中skill之后，显示skill详情、入参、输出、使用说明。
- 顶部功能：Search skills（搜索skill）、Sync from files（从源码文件同步更新skill清单）。

2. 核心使用原则
- Skill = 封装好嘅可复用能力，一次开发，多次调用。
- 新任务唔需要写新代码：优先喺Skill Library搜索现有skill，重用；冇对应skill先新建。
- 每个skill有固定输入输出，可变业务指令放喺prompt/参数，唔改skill底层逻辑。

3. 标准操作流程（worker要跟呢个顺序）
① 先搜索：用Search skills输入关键词，检索有无现成skill可以解决当前需求。
② 阅读skill详情：确认入参、返回结果、限制、适用场景。
③ 编排任务：喺DB任务中心，将skill编排进任务步骤，配置prompt同selector等参数。
④ 执行测试：运行任务，观察日志。
⑤ 失败处理：如果skill执行出错，先检查参数/prompt；确认系skill本身缺陷，先更新源码，再点【Sync from files】同步回Skill Library。

4. 分工边界（好重要）
✅ Skill 负责：固定、确定性、机械底层逻辑（截图、调用VL、坐标解析、重试、异常降级、数据库读写）
✅ Prompt / 任务参数 负责：可变语义（识别目标、输出格式、业务描述）
❌ 唔好将固定循环、重试、异常捕获呢类底层逻辑写落prompt，应该封装成skill。

5. 当要新增技能（例如vision_captcha_solve）
① 写好skill源码。
② 注册进对应catalog（建议放core/general）。
③ 点【Sync from files】同步到Skill Library面板。
④ 之后所有DB任务都可以直接调用呢个skill。

6. 友好使用约定
- 每次用skill前，先确认skill能力边界，唔好强行令skill处理佢唔支持嘅场景。
- 遇到识别类任务：skill负责执行流程，prompt控制识别目标。
- 遇到CAPTCHA、网页自动化：优先复用vision_captcha_solve，只换prompt同DOM selector。
- 记录每一次skill调用结果同埋prompt调优记录，方便后续复用。

7. 输出要求
每次操作Skill Library之后，简短汇报：拣选嘅skill名称、catalog、入参、预期结果、风险备注。

## 精简版（适合放喺skill描述字段）

Skill Library 系中央复用技能库。
使用流程：搜索skill → 查阅skill详情 → 编排入DB任务 → 执行测试。
Skill承载固定底层执行逻辑；Prompt承载可变业务语义，唔将循环/异常逻辑写进prompt。
新增skill写完源码后，使用「Sync from files」同步到面板。
新任务优先复用现有skill，唔重复开发。

## 快速拣skill判断

当你接到一个任务，先判断：
1. 呢个动作系固定重复流程？ → 做成skill，放Skill Library
2. 只系语义、目标、提示词变化？ → 唔使做skill，净喺DB任务修改prompt参数就得

---
name: test_skill
description: 用于验证 skills 注册、system message 可见性和 load_skill 工具加载的测试技能。
---

# Test Skill

本技能仅用于 Step 3 开发期验收。

使用意图：
- 当模型需要确认当前 skills 是否已成功注入 system message 时，可识别本技能名称。
- 当模型需要通过 `load_skill` 读取完整技能正文时，可加载本文件。

执行要求：
- 不代表真实业务技能。
- 不参与权重判断。
- 不自动触发任何动作。
- 只作为 `/chat` 调试可见性的测试样本。

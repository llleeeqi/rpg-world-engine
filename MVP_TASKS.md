# MVP Tasks

## Done In Current Skeleton

- [x] 世界文件夹创建
- [x] manifest 世界锁和基础信息
- [x] PC 网页首页和三栏工作台
- [x] 角色池手动添加
- [x] 最多 3 个角色输入槽
- [x] DM 指令、推进级别、发散程度
- [x] 草稿候选生成
- [x] 接受候选写事件、记忆、状态和 revision
- [x] 重新生成面板
- [x] 可重建 SQLite FTS 索引
- [x] 粘贴文本/预设/角色的轻量导入接口
- [x] 酒馆角色卡 PNG / JSON / TXT 文件上传导入
- [x] SillyTavern 世界书 / Lorebook JSON 条目拆分为 lore 明文文件
- [x] 角色卡内嵌 character_book 条目导入 lore 层
- [x] 导入后核对完成前禁止推演
- [x] 导入核对逐问回答会话
- [x] OpenAI-compatible LLM provider 配置入口
- [x] 多 agent 调用边界与 fallback
- [x] 多 agent 本地检索上下文包
- [x] Judge 输出归一化和校验告警
- [x] Revision 列表与线性回滚
- [x] PC GM 面板明文文件浏览和搜索
- [x] PC GM 面板明文文件编辑
- [x] 记忆/状态 patch 浏览与撤回最新回合
- [x] 单个记忆/状态 patch 反向应用 UI
- [x] Memory Writer 独立调用、结构化记忆计划和校验 fallback
- [x] 世界书 / 预设 / 角色导入核对答案自动写回明文文件
- [x] Agent 池预算、休眠/合并模式和 16 独立档案强提醒
- [x] PC 角色池 agent 模式编辑并写回角色状态文件
- [x] 接受回合时维护 previous_scene 和 scene_log 连续性记录
- [x] 地点状态卡创建、注入生成上下文并随回合 patch 可撤回
- [x] 每世界明文骰子规则文件和生成器规则注入
- [x] PC GM 面板骰子规则编辑入口
- [x] 只重写正文时冻结 Turn Outcome，仅重跑 Writer
- [x] 重跑保留骰子时在本地和 LLM compose 层强制复用旧骰子
- [x] 编辑本轮输入二级状态：取消、重新生成、重新判定随机生成
- [x] 接受回合后记录 resolved DM 指令并在 GM 面板展示
- [x] 反向应用 patch 时同步移除对应 DM 指令记录
- [x] PC 端统一 busy/loading、成功提示和错误提示
- [x] 本地 HTTP server 健康接口、首页静态文件和世界列表 API 运行态检查
- [x] HTTP 端到端 smoke：创建世界、角色、草稿、接受、文件、搜索和索引
- [x] 世界 ZIP 明文导出入口和端到端 zip 内容验证
- [x] 世界 ZIP 明文导入入口和导入/导出往返 HTTP smoke
- [x] PC 浏览器实机 smoke：Qt WebEngine 打开页面、生成候选、验证编辑本轮输入二级按钮

## Next Engineering Steps

- [ ] 更细的视觉回归截图和键盘操作打磨

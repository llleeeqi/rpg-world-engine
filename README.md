# RPG World Engine

我是 llleeeqi。这个东西是我想做的一个本地优先文字 RPG 世界系统。

简单说，我不太想再做那种“一个聊天窗口塞一堆设定，然后越跑越慢、越跑越飘”的玩法。我想要的是一个按轮次推进的世界：我可以接管角色写第一人称行动，也可以用 DM 指令给世界一点全局干预；系统先推一版候选出来，我看着行再接受，接受了才真的写进世界文件。

现在这版先别想太大，先偏个人向，先 PC 网页端，先把能长期玩的骨架跑通。

## 我现在做到了什么

- 世界就是一个文件夹，明文是权威存档，SQLite 只做可重建缓存。
- 每轮最多接管 3 个角色，外加一个 DM 指令框。
- 角色不是全塞给一个模型糊弄，有独立 agent 名额，超过预算的并入 NPC Ensemble。
- 有骰子规则，DM 硬指令可以覆盖骰子，弱介入就让骰子判断。
- 候选草稿不直接入库，我接受之后才写事件、记忆、状态和 revision。
- 可以重新生成、只重写正文、保留骰子重跑、重掷骰子重跑。
- 编辑本轮输入时有二级状态：取消、重新生成、重新判定随机生成。
- 角色、地点、场景都有状态卡，尽量减少衣服、位置、伤势、场景物件漂移。
- 可以导入酒馆角色卡 PNG/JSON/TXT、世界书/Lorebook、预设提示词。
- 导入后先核对，核对答案会写回明文角色/世界/预设文件。
- GM 面板能看文件、搜文件、改文件、回滚 revision、撤回最新回合、反向应用 memory patch。
- ZIP 明文导入导出，`.cache` 和未采纳草稿不会跟着乱跑。

## 怎么跑

我尽量让它轻一点。日常运行不需要 Node 常驻，也不用先装一堆 Python 包。

```bash
python3 server.py
```

打开：

```txt
http://127.0.0.1:54925
```

端口冲突就换一下，比如：

```bash
RPG_WORLD_PORT=54924 python3 server.py
```

## Docker

也可以直接打 Docker。容器里默认监听 `54925`，存档挂到 `/data`。

```bash
docker build -t rpg-world-engine:latest .
docker run --rm -p 54925:54925 -v "$PWD/data:/data" rpg-world-engine:latest
```

或者：

```bash
docker compose up --build
```

如果我把镜像推到 GHCR，会是这个地址：

```txt
ghcr.io/llleeeqi/rpg-world-engine:latest
```

## 存档结构

默认存档在：

```txt
data/worlds/<world_id>/
```

里面的 `current/` 是当前世界，`revisions/` 是线性快照，`.cache/index.sqlite` 是可删可重建的搜索缓存。

我默认不提交 `data/`、`config.json`、`.env`，因为这里面很容易有个人存档和 API key。

## LLM 配置

没有 API key 也能跑，系统会走本地 fallback，至少完整流程能通。

要接 OpenAI-compatible provider，可以这样：

```bash
cp config.example.json config.json
export DEEPSEEK_API_KEY=...
python3 server.py
```

## 测试

离线 smoke：

```bash
python3 tests/smoke.py
```

HTTP smoke：

```bash
python3 server.py
python3 tests/http_smoke.py
```

PC 浏览器 smoke 会真的用 Qt WebEngine 打开页面，主要看三栏布局、候选生成、编辑本轮输入那几个按钮：

```bash
QT_QPA_PLATFORM=xcb xvfb-run -a python3 tests/browser_smoke.py
```

这条浏览器测试需要系统包 `python3-pyqt6.qtwebengine` 和 `xvfb`。只是玩的话不用装。

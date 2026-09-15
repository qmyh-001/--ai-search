# 佛脚AI搜题（fojiao-ai-search）

在佛脚刷题等刷题App里，通过**悬浮球 + DeepSeek API** 搜题看解题思路，体验类似大学搜题酱/夸克悬浮球搜题。

Python（Kivy + Buildozer + pyjnius）编写，目标设备：**iQOO 11（Android 13+，arm64-v8a）**。

## 工作原理

```
┌──────────┐  点悬浮球   ┌─────────────┐   JPEG(base64)   ┌──────────────────┐
│ 佛脚刷题 │ ─────────▶ │ MediaProjection│ ───────────────▶ │ deepseek-flash    │ 截图识题(OCR)
│ (前台App) │           │  截取整屏     │                  │ (DeepSeek 视觉模型)│
└──────────┘           └─────────────┘                  └────────┬─────────┘
     │                                                            │ 题目文本
     │  或：长按复制题目 → 面板【读剪贴板】                            ▼
     │                                                   ┌──────────────────┐
     │                                                   │ deepseek-chat /   │ 解答+解题思路
     │                                                   │ deepseek-reasoner │
     │                                                   └────────┬─────────┘
     ▼                                                            ▼
┌──────────────────────── 悬浮答案面板（可拖动/最小化/复制/手动输入）────────────────────┐
```

- **悬浮球/悬浮面板**：`SYSTEM_ALERT_WINDOW` + `WindowManager`（TYPE_APPLICATION_OVERLAY），pyjnius 直接操作 Android 原生控件
- **截屏**：`MediaProjection`，在 App 前台授权一次后持有复用（用 `android.activity.bind(on_activity_result=...)` 接收授权回调）
- **识题**：DeepSeek 官方视觉模型 `deepseek-flash`，截图直接 base64 上传，无需本地 OCR
- **解题**：`deepseek-chat`（快）或 `deepseek-reasoner`（思路更细，慢）
- **读文字**：剪贴板方式（Android 10+ 限制后台读剪贴板，需先让面板获得焦点，App 内已处理）

## 文件结构

| 文件 | 说明 |
|---|---|
| `main.py` | Kivy 主界面：权限引导、API 设置、使用说明、桌面调试 |
| `android_native.py` | 原生桥接：悬浮球、悬浮面板、MediaProjection 截屏、剪贴板、Toast |
| `ai_core.py` | DeepSeek API 客户端（识图 + 解题），纯标准库 |
| `buildozer.spec` | 打包配置 |
| `.github/workflows/main.yml` | GitHub Actions 自动打包 |

> **目录结构约定**：本文件夹的内容就是仓库根目录的内容，两者一一对应。
> 上传时请选中文件夹**里面的文件**拖到仓库根目录，不要把文件夹本身拖进去
> （否则会多出一层 `fojiao-ai-search/` 目录，构建工具就找不到 `buildozer.spec`）。

## 如何构建 APK

### 方式 A：GitHub Actions 自动打包（推荐，Windows 上唯一省事的路）
1. 把项目文件传到 GitHub 仓库（见上一节）
2. Actions 自动执行 `yes | buildozer -v android debug`
3. **首次约 20~40 分钟**（要下载 Android SDK/NDK 并编译 Python/Kivy/pyjnius）
4. 构建完成后，在该次运行的**底部 Artifacts** 下载 `fojiao-ai-search-apk`
5. 解压得到 `.apk`，传到手机安装（选择"仍要安装"）

> 不要用 `ArtemSBulgakov/buildozer-action`：它的 Docker 镜像依赖已废弃的
> `ppa:openjdk-r` 源，镜像构建直接失败（报 `Docker build failed with exit code 1`）。

### 方式 B：本地 WSL
```bash
# WSL Ubuntu 内
sudo apt update && sudo apt install -y python3-pip build-essential git zip unzip openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev libncurses-dev cmake
pip install buildozer cython==0.29.36
cd fojiao-ai-search
yes | buildozer -v android debug   # 首次会下载 SDK/NDK，较久
# 产物： bin/fojiaoaisearch-1.0.0-arm64-v8a-debug.apk
```

## iQOO 11 使用步骤

1. 安装 APK，打开「佛脚AI搜题」
2. 到 [platform.deepseek.com](https://platform.deepseek.com) 创建 **API Key**，在 App「API 设置」填入
3. 点「① 申请悬浮窗权限」→ 系统设置里允许
4. 点「② 开启截屏授权」→ 系统弹窗选「立即开始」（完全杀掉App后需重新点一次）
5. 点「③ 启动悬浮球」→ 自动退到后台 → 打开佛脚刷题
6. 点悬浮球"搜"：
   - 默认**截图搜题**：截全屏 → deepseek-flash 识题 → deepseek 解答 → 悬浮面板显示
   - 或长按选中题目文字复制 → 面板里点【读剪贴板】
   - 需要手动改题时，点输入框右侧的 **⌨** 弹出键盘（面板窗口本身可获焦，输入框也能直接点）
   - 面板可拖动、`—` 最小化、`✕` 关闭
7. **重要**：设置 → 电池 → 后台耗电管理 → 本应用允许后台高耗电；开启自启动；不要从最近任务划掉

## 在 GitHub 网页上更新文件（无需安装 Git）

**上传普通文件**

1. 打开仓库页（根目录）
2. **Add file → Upload files**
3. 打开本地文件夹 `C:\Users\24966\.zcode\workspace\default\fojiao-ai-search`，
   选中里面的文件（`main.py`、`ai_core.py`、`android_native.py`、`buildozer.spec`、`README.md`）
   拖进网页 —— 同名文件会被直接覆盖
4. 页面底部点 **Commit changes**

**改一两行：网页编辑**

1. 点开要改的文件 → 点右上角**铅笔图标**
2. 修改内容 → 点 **Commit changes...** → 再点 **Commit changes**

**上传工作流文件要注意**

GitHub 的拖拽上传**会跳过以点开头的隐藏目录**，所以 `.github/workflows/`
下的文件**拖不上去**，必须手动创建：**Add file → Create new file**，
文件名框里直接输入 `.github/workflows/main.yml`（输入 `/` 会自动变成目录层级），
再粘贴内容并提交。

提交后 Actions 会自动触发；若没有，去 **Actions → Build Android APK → Run workflow** 手动触发。

## 常见问题

| 问题 | 原因/解决 |
|---|---|
| 构建报 `Aidl not found` | `buildozer.spec` 缺 `android.accept_sdk_license = True`，导致 SDK 许可协议等待交互输入、build-tools 没装上 |
| 构建报 `Docker build failed with exit code 1` | 用的是 `ArtemSBulgakov/buildozer-action`，其镜像依赖已失效的 `ppa:openjdk-r`。改用本仓库当前的工作流（原生 apt 安装 buildozer） |
| 点悬浮球提示"请先完成截屏授权" | App 进程被系统回收，重新打开App点一次「② 开启截屏授权」即可 |
| 提示"截屏授权已被系统回收" | 锁屏或切后台会触发，回 App 重新授权一次 |
| 读不到剪贴板 | Android 10+ 限制：点面板里的【读剪贴板】（该按钮点下去时面板已获焦），或直接粘贴到输入框 |
| 报 `certificate verify failed` | Android 上的 Python 找不到系统 CA 证书，需 `certifi` 在 requirements 里（已配置） |
| 识别不准 | 重拍一次；截图会缩放到1280宽再上传，如需更清晰可改 `android_native.py` 中的 1280 |
| 接口报 401 | API Key 填错；Base URL 保持 `https://api.deepseek.com` |
| 悬浮球消失 | 被系统杀了进程，见上文电池设置 |

## 参考的开源项目

- [ScanSearch（问一下）](https://github.com/PuZhiweizuishuai/ScanSearch) — 无障碍读屏 + 悬浮窗 + 大模型搜题（本项目借鉴其交互设计）
- [SoutiAssistant](https://github.com/qingtianes/SoutiAssistant) — 框选OCR悬浮窗搜题（桌面端）
- [exameow](https://github.com/heshengtao/exameow) — 拍照/拍屏/录屏 AI 搜题
- [chaoxing-tool-client](https://github.com/PBK-B/chaoxing-tool-client) — 悬浮窗搜题/选中文本搜题
- DeepSeek 视觉接口文档：https://api-docs.deepseek.com/zh-cn/guides/vision/

## 免责声明

仅供个人学习与技术研究。搜题内容会上传至 DeepSeek 服务器，请勿用于违反题目来源平台规则或考试的场合。

## 后续可扩展

- 无障碍服务读屏取题（像 ScanSearch 那样免截图，准确率更高）
- 框选区域截图（只截题目部分，token 更省）
- 悬浮面板渲染 Markdown、TTS 播报、本地题库缓存

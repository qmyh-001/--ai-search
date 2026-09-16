# 佛脚AI搜题（fojiao-ai-search）

在佛脚刷题等刷题App里，通过**悬浮球 + DeepSeek API** 搜题看解题思路，体验类似大学搜题酱/夸克悬浮球搜题。

Python（Kivy + Buildozer + pyjnius）编写，按 **arm64-v8a** 打包，适用于 Android 10+（minSdk 29）的手机。

## 工作原理

```
┌──────────┐  点悬浮球   ┌─────────────┐   JPEG(base64)   ┌──────────────────┐
│ 佛脚刷题 │ ─────────▶ │ MediaProjection│ ───────────────▶ │ deepseek-flash    │ 截图识题(OCR)
│ (前台App) │           │  截取整屏     │                  │ (DeepSeek 视觉模型)│
└──────────┘           └─────────────┘                  └────────┬─────────┘
     │                                                            │ 题目文本
     │  或：长按复制题目 → 面板【读剪贴板】                            ▼
     │                                                   ┌──────────────────┐
     │                                                   │ deepseek-flash    │ 解答+解题思路
     │                                                   │ （可在设置里换）    │
     │                                                   └────────┬─────────┘
     ▼                                                            ▼
┌──────────────────────── 悬浮答案面板（可拖动/最小化/复制/手动输入）────────────────────┐
```

- **悬浮球/悬浮面板**：`SYSTEM_ALERT_WINDOW` + `WindowManager`（TYPE_APPLICATION_OVERLAY），pyjnius 直接操作 Android 原生控件
- **截屏**：`MediaProjection` + 独立进程前台服务；`hook.py` 为 service 注入 `foregroundServiceType="mediaProjection"`，并把 Java `ProjectionCallback` 助手编译进 APK。授权成功后创建一次常驻截屏会话并自动显示悬浮球
- **识题**：DeepSeek 官方视觉模型 `deepseek-flash`，截图直接 base64 上传，无需本地 OCR
- **解题**：默认 `deepseek-flash`（与识图同一个模型，可在「API 设置」里改成别的可用模型）
- **读文字**：剪贴板方式（Android 10+ 限制后台读剪贴板，需先让面板获得焦点，App 内已处理）
- **界面**：亮色 iOS 风 —— 浅灰底 + 白色圆角卡片 + 柔和阴影 + SF 蓝 `#0A84FF`；
  图标用 **Lucide 图标字体**（`lucide.ttf`）；页面滑动切换、进入淡入、按钮按下压暗、
  iOS 八段加载指示器。悬浮球与悬浮面板是原生 Android 控件，同样做成浅色 iOS 风

## 文件结构

| 文件 | 说明 |
|---|---|
| `main.py` | Kivy 主界面：权限引导、API 设置、使用说明、桌面调试 |
| `android_native.py` | 原生桥接：悬浮球、悬浮面板、MediaProjection 截屏、剪贴板、Toast |
| `ai_core.py` | DeepSeek API 客户端（识图 + 解题），纯标准库 |
| `buildozer.spec` | 打包配置：Android 权限、前台服务、p4a 构建钩子 |
| `hook.py` | 构建时注入前台服务类型，并安装 Java MediaProjection 回调助手 |
| `service.py` | 独立进程的 MediaProjection 前台服务入口 |
| `java/com/fojiaoai/fojiaoaisearch/ProjectionCallback.java` | 继承 `MediaProjection.Callback`，把停止事件转发给 Python |
| `NotoSansSC-Regular.otf` | 中文字体（约 8 MB）。Kivy 自带字体不含汉字，必须随包打进 APK |
| `lucide.ttf` | 图标字体（Lucide，约 875 KB，来自 github.com/lucide-icons/lucide，ISC 许可）。界面图标都用它；**缺失时自动不显示图标，不影响任何功能** |
| `.github/workflows/main.yml` | GitHub Actions 自动打包 |

> **目录结构约定**：本文件夹的内容就是仓库根目录的内容，两者一一对应。
> 上传时请选中文件夹**里面的文件**拖到仓库根目录，不要把文件夹本身拖进去
> （否则会多出一层 `fojiao-ai-search/` 目录，构建工具就找不到 `buildozer.spec`）。

## 如何构建 APK

### 方式 A：GitHub Actions 自动打包（推荐，Windows 上唯一省事的路）
1. 把项目文件传到 GitHub 仓库（见上一节）
2. Actions 自动执行 `yes | buildozer -v android debug`
3. **首次约 30~50 分钟**（要下载 Android SDK/NDK 并编译 Python/Kivy/pyjnius）
4. 构建完成后，在该次运行的**底部 Artifacts** 下载 `fojiao-ai-search-apk`
   （同时会自动挂到仓库的 **Releases** 页，那里可以匿名直接下载，方便取用）
5. 解压得到 `.apk`，传到手机安装（选择"仍要安装"）

> **工具链是刻意钉死版本的，不要随手升级：**
> `buildozer.spec` 里 `p4a.branch = v2024.01.21`（对应 Python 3.11.5），
> 工作流里 `buildozer==1.5.0` + `cython==0.29.33`，`requirements` 里 `kivy==2.3.0`。
> 原因：python-for-android 的 master 分支已切到 Python 3.14.2，但其 kivy recipe
> 仍把 Cython 限制在 `<=3.0.12`（Cython 3.0.x 最高只支持 Python 3.12），
> 二者冲突会导致 kivy 的 C 文件编译时报满屏
> `error: too few arguments to function call, expected 6, have 5` 和
> `error: call to undeclared function '_PyInterpreterState_GetConfig'`。
> 这是上游当前的状态，不是本项目代码的问题。

> 不要用 `ArtemSBulgakov/buildozer-action`：它的 Docker 镜像依赖已废弃的
> `ppa:openjdk-r` 源，镜像构建直接失败（报 `Docker build failed with exit code 1`）。

### 方式 B：本地 WSL
```bash
# WSL Ubuntu 内
sudo apt update && sudo apt install -y python3-pip build-essential git zip unzip openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev libncurses-dev cmake
pip install "cython==0.29.33" "buildozer==1.5.0"
cd fojiao-ai-search
yes | buildozer -v android debug   # 首次会下载 SDK/NDK，较久
# 产物： bin/fojiaoaisearch-1.0.0-arm64-v8a-debug.apk
```

## 使用步骤

1. 安装 APK，打开「佛脚AI搜题」
2. 到 [platform.deepseek.com](https://platform.deepseek.com) 创建 **API Key**，在 App「API 设置」填入
3. 点「① 申请悬浮窗权限」→ 系统设置里允许
4. 点「② 开启截屏授权」→ 按系统提示继续并选择要共享的应用；授权成功后悬浮球会自动出现（完全杀掉 App 或锁屏后需重新授权）
   - **如果选了「共享一个应用」而被限制**（微信等会被系统判为隐私应用）：按系统弹窗提示**手动解除限制**，或者改选「共享整个屏幕」
   - **这种模式下只有被选中的应用在前台时才截得到画面**，所以在别的界面点悬浮球会截到空画面（全黑）→ 在要搜题的 App 界面里点悬浮球；嫌麻烦就用「共享整个屏幕」
   - 如果目标应用自己禁止被截屏（系统隐私保护），任何方式都拿不到画面 → 改用面板上的【读剪贴板】
5. 打开佛脚刷题，点悬浮球"搜"即可；如果只使用「最新截图」或「剪贴板搜题」，也可以手动点「③ 启动悬浮球」
6. 点悬浮球"搜"后：
   - 默认**截图搜题**：截屏 → deepseek-flash 识题 → 解答 → 悬浮面板显示
   - 出答案后**可以在面板输入框里接着追问**（比如"为什么选 B"），会带着这道题继续回答
   - 想手动输题或粘贴：点面板输入框直接弹键盘，打完点圆形 `问`
   - 面板右上角 `↙↗` 拖动可改大小（挡题时收小），`—` 最小化、`×` 关闭
7. **重要**：设置 → 电池 → 后台耗电管理 → 允许本应用后台高耗电；开启自启动；不要从最近任务划掉（否则悬浮球和截屏授权会失效）

## 在 GitHub 网页上更新文件（无需安装 Git）

**上传普通文件**

1. 打开仓库页（根目录）
2. **Add file → Upload files**
3. 把下面这些文件传到**仓库根目录**（同名覆盖；Java 文件要保留完整目录层级）：

   | 要传的文件 | 说明 |
   |---|---|
   | `main.py` | 主界面（亮色 iOS 风）、设置、使用说明 |
   | `android_native.py` | 悬浮球 / 悬浮面板 / 截屏 / 追问 / 黑帧识别 |
   | `ai_core.py` | DeepSeek 客户端（识图 + 解题 + 会话上下文） |
   | `hook.py` | 构建钩子：注入前台服务类型 + 安装 Java 回调助手 |
   | `service.py` | 截屏用的前台服务入口 |
   | `buildozer.spec` | 打包配置（**改了它 CI 缓存会失效，重建要 30~50 分钟**） |
   | `lucide.ttf` | 界面图标字体。**CI 不会自动下载，必须传** |
   | `README.md` | 本文档 |
   | `.github/workflows/main.yml` | CI 流程（隐藏目录，拖拽传不上去，见下面"上传工作流文件要注意"） |
   | `java/com/fojiaoai/fojiaoaisearch/ProjectionCallback.java` | MediaProjection 回调助手，**必须保留 `java/com/fojiaoai/fojiaoaisearch/` 目录层级** |

   **不用传**：`NotoSansSC-Regular.otf`（CI 构建时自动下载）、`__pycache__/`、
   `bin/`、`.buildozer/`（都是构建产物）。
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

## 可调项（App 内「API 设置」）

| 项目 | 说明 |
|---|---|
| **识图模型** | 截图识别用，默认 `deepseek-flash`（视觉模型） |
| **解题模型** | 默认 `deepseek-flash`。可直接手动输入任意可用模型名 |
| **拉取可用模型列表** | 点按钮调 `GET /models` 拉取官方模型列表，弹窗里点一下自动填入；拉取失败也能手动输入 |
| **思考强度** | 默认「高」（即官方默认 `high`）；可选 关闭 / 低 / 高 / 最高（`max`）。识图始终强制关闭思考以求速度 |
| **解题提示词** | 系统提示词，内置默认已按**医学类题目**调优（单选/多选/判断/名词解释/简答/病例分析各自的作答结构）。留空用内置默认 |
| **识图提示词** | 截图 OCR 的提示词，留空用内置默认 |
| **恢复默认提示词** | 一键把两个提示词恢复成内置版本 |

> 提示词如果与内置默认完全一致，保存时会存为空值——这样以后升级默认提示词能自动跟随。

## 常见问题

| 问题 | 原因/解决 |
|---|---|
| 点「② 开启截屏授权」报 `Media projections require a foreground service of type ...MEDIA_PROJECTION` | Android 14 起的平台硬要求：截屏必须由一个声明 `foregroundServiceType="mediaProjection"` 的前台服务承载，且服务要先于 `getMediaProjection()` 运行。本项目用 `services = medcap:service.py:foreground` + `hook.py`（构建时给清单注入类型属性）满足该要求。**注意 service 必须保留 `android:process`（独立进程）**，否则同进程内两个 Python 解释器会直接 SIGSEGV |
| 授权后仍提示未成功 | p4a 的服务在独立进程里要先起 Python 解释器（1 秒以上）才调 `startForeground()`，App 会分次重试约 8 秒；仍失败可重新点一次「②」，或临时改用【最新截图】 |
| 锁屏后截屏失效 | Android 16 起锁屏会自动终止截屏会话（所有应用），需重新授权。App 已注册 `MediaProjection.Callback`，会在面板提示重新授权 |
| 授权时选微信等应用被限制/灰掉（"隐私应用"） | Android 14 起的系统行为：部分应用被判为隐私应用，选「共享一个应用」时受限。按系统弹窗提示手动解除限制，或改选「共享整个屏幕」（整屏共享不受单个应用限制）。App 在第一次点「②」时也会提示这一点 |
| 截图出来是空的/全黑 | 两个常见原因：① 授权时选的是「共享一个应用」——这种模式下只有那个应用在前台时才截得到画面，所以要在**要搜题的 App 界面里**点悬浮球，或者改选「共享整个屏幕」；② 该应用自己禁止被截屏（`FLAG_SECURE`，系统隐私保护），任何方式都拿不到画面。App 会自动识别全黑帧并给出这段提示，也会建议改用【读剪贴板】 |
| 按安卓返回键，程序直接退到后台/像"退出" | Kivy 默认对返回键执行 `moveTaskToBack`。已在 `main.py` 的 `_on_key()` 里接管：子页面→回主界面，弹窗→关弹窗，只有本来在主界面才交给系统 |
| 悬浮球不出现，日志报 `No methods called setText ... matching your arguments` | pyjnius 只自动转换声明为 `String` 的参数，**不转换 `CharSequence`**。所有 `setText`/`setHint`/`Toast.makeText`/`ClipData.newPlainText` 的字符串参数必须用 `android_native._s()` 包一层 |
| 界面汉字全是方框 | 缺中文字体。`NotoSansSC-Regular.otf` 必须在仓库根目录，且 `source.include_exts` 里要有 `otf`。字体在 `main.py` 的 `_register_cjk_font()` 里注册为默认字体 |
| gradle 报 `Android Gradle plugin requires Java 17 to run. You are currently using Java 11` | 运行器默认 JAVA_HOME 是 Java 11。工作流里必须有 `actions/setup-java@v4`（java-version: '17'），别删 |
| 编译 kivy 时报大量 `too few arguments to function call` / `call to undeclared function '_PyInterpreterState_GetConfig'` | p4a 用了 Python 3.14 而 Cython 被锁在 ≤3.0.12（不认识 3.14 的 API）。必须保留 `p4a.branch = v2024.01.21`，别删 |
| 构建报 `Aidl not found` | `buildozer.spec` 缺 `android.accept_sdk_license = True`，导致 SDK 许可协议等待交互输入、build-tools 没装上 |
| 构建报 `Docker build failed with exit code 1` | 用了 `ArtemSBulgakov/buildozer-action`，其镜像依赖已失效的 `ppa:openjdk-r`。改用本仓库自带的工作流 |
| 点悬浮球提示"还没有截屏授权" | App 进程被系统回收，或授权已被系统回收。重新打开 App 点一次「② 开启截屏授权」即可；也可先用【最新截图】 |
| 提示"截屏授权已被系统回收" | 锁屏或切后台会触发，回 App 重新授权一次。若重复点「②」后仍报这句，说明新授权把旧投影顶掉了，重新点一次即可 |
| 读不到剪贴板 | Android 10+ 限制：点面板里的【读剪贴板】（该按钮点下去时面板已获焦），或直接粘贴到输入框 |
| 报 `certificate verify failed` | Android 上的 Python 找不到系统 CA 证书，需 `certifi` 在 requirements 里（已配置） |
| 识别不准 | 重拍一次；截图上传前会缩到 1280 宽再转 JPEG（`android_native.py` 里 `_prepare_image(max_w=1280)` 与 `_do_capture` 各有一处），如需更清晰可调大 |
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

[app]

# 应用信息
title = 佛脚AI搜题
package.name = fojiaoaisearch
package.domain = com.fojiaoai
source.dir = .
# otf/ttf 必须包含，否则中文字体文件不会被打进 APK
source.include_exts = py,png,jpg,kv,atlas,json,txt,otf,ttf
version = 1.0.0

# 依赖：android 模块(activity/runnable/permissions)由 SDL2 引导自动提供，无需列出
# certifi 提供 CA 证书包（kivy recipe 的 python_depends 里也有，显式列出更清楚）
requirements = python3,kivy==2.3.0,pyjnius,certifi

# 【关键】把 python-for-android 钉在 v2024.01.21（对应 Python 3.11.5）。
# 不钉的话 buildozer 默认用 p4a 的 master 分支，而 master 已切到 Python 3.14.2，
# 其 kivy recipe 却仍把 Cython 限制在 <=3.0.12（Cython 3.0.x 最高只支持 Python 3.12）。
# 两者冲突会让 kivy 的 Cython 生成的 C 文件编译时满屏报：
#   error: too few arguments to function call, expected 6, have 5
#   error: call to undeclared function '_PyInterpreterState_GetConfig'
p4a.branch = v2024.01.21

# 只打 arm64（iQOO 11 为骁龙8 Gen2，arm64-v8a），加快构建
android.archs = arm64-v8a

# target 33（Android 13）：保守取值，减少新系统行为变更的影响；
# min 29 覆盖 Android 10+。
# 注意：target 33 并不能规避 Android 14+ 对截屏的前台服务要求
# （实测 Android 16 上 targetSdk=33 仍会抛 SecurityException），
# 该要求由下面的 services + hook.py 满足。
android.api = 33
android.minapi = 29

# INTERNET 联网；SYSTEM_ALERT_WINDOW 悬浮窗（需用户在系统设置里手动允许）
# READ_MEDIA_IMAGES / READ_EXTERNAL_STORAGE：用于「最新截图」方式读相册里的截图
# FOREGROUND_SERVICE* / POST_NOTIFICATIONS：Android 14+ 截屏要求的前台服务
android.permissions = android.permission.INTERNET,android.permission.SYSTEM_ALERT_WINDOW,android.permission.READ_MEDIA_IMAGES,android.permission.READ_EXTERNAL_STORAGE,android.permission.FOREGROUND_SERVICE,android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION,android.permission.POST_NOTIFICATIONS

# 前台服务：Android 14 起截屏必须由 foregroundServiceType=mediaProjection
# 的前台服务承载，否则 getMediaProjection() 抛 SecurityException。
# 类型属性 p4a v2024.01.21 不支持写在这里，由 hook.py 在构建时注入清单。
services = medcap:service.py:foreground
p4a.hook = hook.py

# 自动接受 Android SDK 许可协议。
# 不设这一项时 sdkmanager 会停下来等待交互输入，导致 build-tools 装不上，
# 最终报 "Aidl not found, please install it."
android.accept_sdk_license = True

orientation = portrait
fullscreen = 0
android.allow_backup = False

#
# Windows 用户推荐用 GitHub Actions 自动构建（见 .github/workflows/build.yml），
# 本地构建请在 WSL/Ubuntu 中安装 buildozer 后执行：
#   buildozer android debug
# 产物在 bin/*.apk
#

[buildozer]

log_level = 2
warn_on_root = 1

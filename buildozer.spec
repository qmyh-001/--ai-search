[app]

# 应用信息
title = 佛脚AI搜题
package.name = fojiaoaisearch
package.domain = com.fojiaoai
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt
version = 1.0.0

# 依赖：android 模块(activity/runnable/permissions)由 SDL2 引导自动提供，无需列出
# certifi 提供 CA 证书包，否则 Android 上 HTTPS 请求会证书校验失败
requirements = python3,kivy==2.3.0,pyjnius,certifi

# 只打 arm64（iQOO 11 为骁龙8 Gen2，arm64-v8a），加快构建
android.archs = arm64-v8a

# target 33（Android 13）：规避 Android 14 对 target34 强制
# MediaProjection 前台服务类型的限制；min 29 覆盖 Android 10+
android.api = 33
android.minapi = 29

# INTERNET 联网；SYSTEM_ALERT_WINDOW 悬浮窗（需用户在系统设置里手动允许）
android.permissions = android.permission.INTERNET,android.permission.SYSTEM_ALERT_WINDOW

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

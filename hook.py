# -*- coding: utf-8 -*-
"""python-for-android 构建钩子（buildozer.spec 里的 p4a.hook 指向本文件）。

用途：给自定义 service 的清单元素补上 foregroundServiceType。

背景：Android 14 起，截屏必须由一个
foregroundServiceType="mediaProjection" 的前台服务承载，否则
getMediaProjection() 直接抛 SecurityException：

    Media projections require a foreground service of type
    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION

而本项目钉的 p4a（v2024.01.21）的 services 语法只支持
:foreground / :sticky，没有 foregroundServiceType 参数（该参数是
后来才加的），所以这里在构建时直接改清单模板。

⚠️ 两个必须遵守的点：
  1. 【保留 android:process】服务必须在独立进程里跑。删掉它会让同一个
     进程里出现两个 Python 解释器（Kivy 应用 + p4a 服务），实测直接
     SIGSEGV 崩溃（tombstone: signal 11, Cause: null pointer dereference）。
  2. 【确定性重建】CI 会缓存 .buildozer，里面可能残留上一次改过的模板，
     所以这里不判断"是否已注入"，而是直接把整段 service 定义重写成
     正确形式——这样无论之前是什么状态都能修正，且天然幂等。

钩子由 p4a 调用：`getattr(hook_module, "before_apk_build")(toolchain)`，
此时工作目录就是 dist 目录（p4a toolchain.py 中
`with current_directory(dist.dist_dir): self.hook("before_apk_build")`），
因此在生成 AndroidManifest.xml 之前改模板即可生效。
"""
import io
import os
import re

MARK = 'android:foregroundServiceType="mediaProjection"'

#: 自定义服务的正确写法（缩进与 p4a 模板保持一致）
SERVICE_BLOCK = (
    '{% for name in service_names %}\n'
    '        <service android:name='
    '"{{ args.package }}.Service{{ name|capitalize }}"\n'
    '                 android:process=":service_{{ name }}"\n'
    '                 ' + MARK + ' />\n'
    '        {% endfor %}'
)

BLOCK_RE = re.compile(
    r'\{%\s*for name in service_names\s*%\}.*?\{%\s*endfor\s*%\}',
    re.S)


def _find_template(toolchain):
    cwd = os.getcwd()
    candidates = [
        os.path.join(cwd, 'AndroidManifest.tmpl.xml'),
        os.path.join(cwd, 'templates', 'AndroidManifest.tmpl.xml'),
    ]
    try:
        candidates.append(os.path.join(toolchain.dist.dist_dir,
                                       'AndroidManifest.tmpl.xml'))
    except Exception:
        pass
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _patch_manifest(toolchain):
    target = _find_template(toolchain)
    if target is None:
        raise RuntimeError(
            '[hook] 找不到 AndroidManifest.tmpl.xml（工作目录 %s）——'
            '构建已中止，避免产出一个无法截屏的包' % os.getcwd())

    text = io.open(target, encoding='utf-8').read()
    if MARK in text and 'android:process=":service_{{ name }}"' in text:
        print('[hook] 清单模板已是正确形式，无需修改')
        return

    new_text, n = BLOCK_RE.subn(SERVICE_BLOCK, text)
    if n == 0:
        # 模板结构变了：退化为最小改动，至少把类型属性补上
        old = 'android:process=":service_{{ name }}" />'
        new = ('android:process=":service_{{ name }}"\n'
               '                 ' + MARK + ' />')
        if old in text:
            new_text = text.replace(old, new)
            n = text.count(old)
    if n == 0 or new_text == text:
        raise RuntimeError(
            '[hook] 没能改写 service 定义——p4a 模板可能变了，请检查 %s'
            % target)

    io.open(target, 'w', encoding='utf-8').write(new_text)
    print('[hook] 已重写 service 定义（注入 %s，保留独立进程）-> %s'
          % (MARK, target))


#: Java 助手在仓库里的位置（相对本文件）
JAVA_REL = os.path.join('java', 'com', 'fojiaoai', 'fojiaoaisearch',
                        'ProjectionCallback.java')

#: 它在 dist 的 Gradle 工程里应放的位置（src/main/java 是默认源码目录）
JAVA_DEST_REL = os.path.join('com', 'fojiaoai', 'fojiaoaisearch',
                             'ProjectionCallback.java')


def _gradle_java_root(toolchain):
    """找到 dist 里 Gradle 工程的 src/main/java 目录。"""
    candidates = [os.path.join(os.getcwd(), 'src', 'main', 'java')]
    try:
        candidates.append(os.path.join(toolchain.dist.dist_dir,
                                       'src', 'main', 'java'))
    except Exception:
        pass
    for p in candidates:
        if os.path.isdir(p):
            return p
    return None


def _install_java(toolchain):
    """把 ProjectionCallback.java 放进 Gradle 源码目录，让它是编译进 APK。

    为什么不走 buildozer.spec 的 android.add_src：改 spec 会让 CI 的
    .buildozer 缓存失效（构建从 3~8 分钟变成 30~50 分钟）。而 Gradle 工程
    本来就有标准的 src/main/java 源码目录，直接把文件写进去同样会被编译。

    没有这个类，Android 14+ 上就注册不了 MediaProjection 回调 —— 表现是
    虚拟显示 state=ON 但一帧都收不到，所以这里缺文件必须让构建失败。
    """
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), JAVA_REL)
    if not os.path.exists(src):
        raise RuntimeError('[hook] 找不到 Java 助手源码：%s' % src)

    root = _gradle_java_root(toolchain)
    if root is None:
        raise RuntimeError(
            '[hook] 找不到 Gradle 源码目录 src/main/java（工作目录 %s）——'
            '构建已中止，避免产出一个截不了屏的包' % os.getcwd())

    dest = os.path.join(root, JAVA_DEST_REL)
    if not os.path.isdir(os.path.dirname(dest)):
        os.makedirs(os.path.dirname(dest))
    with io.open(src, encoding='utf-8') as f:
        text = f.read()
    io.open(dest, 'w', encoding='utf-8').write(text)
    print('[hook] 已安装 Java 回调助手 -> %s' % dest)


def before_apk_build(toolchain):
    """p4a 钩子入口：APK 构建前（清单生成前）执行。"""
    print('[hook] before_apk_build 开始')
    _patch_manifest(toolchain)
    _install_java(toolchain)
    print('[hook] before_apk_build 完成')
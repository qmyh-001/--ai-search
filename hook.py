# -*- coding: utf-8 -*-
"""python-for-android 构建钩子（buildozer.spec 里的 p4a.hook 指向本文件）。

用途：给自定义 service 的清单元素补上 foregroundServiceType。

背景：Android 14 起，截屏必须由一个
foregroundServiceType="mediaProjection" 的前台服务承载，否则
getMediaProjection() 直接抛 SecurityException。而本项目钉的 p4a
（v2024.01.21）的 services 语法只支持 :foreground/:sticky，
没有 foregroundServiceType 参数（该参数是后来才加的），
所以这里在构建时直接改清单模板。

钩子由 p4a 调用：`getattr(hook_module, "before_apk_build")(toolchain)`，
此时工作目录就是 dist 目录（见 p4a toolchain.py 的
`with current_directory(dist.dist_dir): self.hook("before_apk_build")`），
因此可以在生成 AndroidManifest.xml 之前改掉模板。
"""
import io
import os

MARK = 'android:foregroundServiceType="mediaProjection"'


def _patch_manifest(toolchain):
    """把 dist 里清单模板的 service 元素改成前台服务（mediaProjection）。"""
    cwd = os.getcwd()
    candidates = [
        os.path.join(cwd, 'AndroidManifest.tmpl.xml'),
        os.path.join(cwd, 'templates', 'AndroidManifest.tmpl.xml'),
    ]
    # 也按 toolchain 的 dist 目录找一遍，避免 cwd 变化导致漏改
    try:
        dist_dir = toolchain.dist.dist_dir
        candidates.append(os.path.join(dist_dir, 'AndroidManifest.tmpl.xml'))
    except Exception:
        pass

    target = None
    for p in candidates:
        if os.path.exists(p):
            target = p
            break
    if target is None:
        raise RuntimeError(
            '[hook] 找不到 AndroidManifest.tmpl.xml，已找过: %s' % candidates)

    text = io.open(target, encoding='utf-8').read()
    if MARK in text:
        print('[hook] 清单模板里已有 foregroundServiceType，跳过')
        return

    before = text
    # 1) 自定义服务：原本是 android:process=":service_xxx"（独立进程），
    #    这里改成前台服务类型，并去掉独立进程（让服务跑在主进程里）
    text = text.replace('android:process=":service_{{ name }}"',
                        MARK)
    # 2) 万一模板写法不同，退而求其次：给 args 里声明的服务类补属性
    if text == before:
        old = '<service android:name="{{ args.package }}.Service{{ name|capitalize }}"'
        new = old + '\n                 ' + MARK
        text = text.replace(old, new)

    if text == before:
        raise RuntimeError(
            '[hook] 没能找到可替换的 service 元素——p4a 模板可能变了，'
            '请检查 %s' % target)

    io.open(target, 'w', encoding='utf-8').write(text)
    print('[hook] 已给 service 注入 %s -> %s' % (MARK, target))


def before_apk_build(toolchain):
    """p4a 钩子入口：APK 构建前（清单生成前）执行。"""
    print('[hook] before_apk_build 开始')
    _patch_manifest(toolchain)
    print('[hook] before_apk_build 完成')

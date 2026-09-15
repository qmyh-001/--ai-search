# -*- coding: utf-8 -*-
"""前台服务入口。

Android 14 起，截屏（MediaProjection）要求应用先运行一个
foregroundServiceType="mediaProjection" 的前台服务，否则
getMediaProjection() 会抛 SecurityException：

    Media projections require a foreground service of type
    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION

本服务不做别的事，只是把这个"前台服务在运行"的条件满足掉。
清单里的类型属性由项目根目录的 hook.py 在构建时注入
（p4a 这个版本还不支持 service 的 foregroundServiceType 参数）。
"""
import time

# p4a 会在服务线程里执行本脚本；保持不退出，服务就一直是前台状态。
# 收到停止请求时进程会被结束，这里的循环随之终止。
while True:
    time.sleep(3600)

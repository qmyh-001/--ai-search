package com.fojiaoai.fojiaoaisearch;

import android.media.MediaProjection;

/**
 * MediaProjection 回调的 Java 助手。
 *
 * 背景：Android 14 (API 34) 起 MediaProjection.Callback 从「接口」改成了
 * 「抽象类」，而 pyjnius 的 PythonJavaClass 内部靠 java.lang.reflect.Proxy，
 * 只能实现接口、无法子类化抽象类。于是 Python 侧永远拿不到一个可用的
 * Callback 实例。
 *
 * 后果不是可选的：Android 14+ 要求 createVirtualDisplay() 之前必须注册回调，
 * 没有回调时系统不往 ImageReader 送帧 —— 实测现象是虚拟显示 state=ON、
 * 投影也持有，但 acquireLatestImage() 永远返回 null（一直报「没收到屏幕画面」）。
 *
 * 所以这里用一小段 Java 兜住：继承 Callback，把 onStop 转发给一个 Python 侧
 * 能实现的接口（PythonJavaClass 实现接口是没问题的）。
 */
public class ProjectionCallback extends MediaProjection.Callback {

    /** Python 侧用 PythonJavaClass 实现这个接口。 */
    public interface Listener {
        void onProjectionStop();
    }

    private final Listener listener;

    public ProjectionCallback(Listener listener) {
        super();
        this.listener = listener;
    }

    @Override
    public void onStop() {
        if (listener != null) {
            listener.onProjectionStop();
        }
    }
}

# -*- coding: utf-8 -*-
"""Android 原生桥接层（仅在 Kivy + pyjnius 环境可用）。

实现：
  - 悬浮球（可拖动，点按触发搜题）—— SYSTEM_ALERT_WINDOW + WindowManager
  - 悬浮答案面板（可拖动/最小化/关闭，内含输入框与按钮）
  - MediaProjection 截屏（App 在前台授权一次，之后后台复用）
  - 剪贴板读写、Toast
桌面环境 import 本模块不会报错，android_native.bridge 为 None。
"""
import os
import threading
import time
import traceback

import ai_core

ANDROID = False
try:
    from jnius import autoclass, cast, PythonJavaClass, java_method
    from android.runnable import run_on_ui_thread
    from android import activity as _android_activity

    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    activity = PythonActivity.mActivity

    JString = autoclass('java.lang.String')

    Context = autoclass('android.content.Context')
    Intent = autoclass('android.content.Intent')
    Uri = autoclass('android.net.Uri')
    ASettings = autoclass('android.provider.Settings')
    View = autoclass('android.view.View')
    MotionEvent = autoclass('android.view.MotionEvent')
    Gravity = autoclass('android.view.Gravity')
    PixelFormat = autoclass('android.graphics.PixelFormat')
    WMLP = autoclass('android.view.WindowManager$LayoutParams')
    LLLP = autoclass('android.widget.LinearLayout$LayoutParams')
    LinearLayout = autoclass('android.widget.LinearLayout')
    ScrollView = autoclass('android.widget.ScrollView')
    TextView = autoclass('android.widget.TextView')
    EditText = autoclass('android.widget.EditText')
    Button = autoclass('android.widget.Button')
    GradientDrawable = autoclass('android.graphics.drawable.GradientDrawable')
    Bitmap = autoclass('android.graphics.Bitmap')
    BitmapFactory = autoclass('android.graphics.BitmapFactory')
    BitmapConfig = autoclass('android.graphics.Bitmap$Config')
    BitmapFormat = autoclass('android.graphics.Bitmap$CompressFormat')
    FileOutputStream = autoclass('java.io.FileOutputStream')
    ImageReader = autoclass('android.media.ImageReader')
    DisplayManager = autoclass('android.hardware.display.DisplayManager')
    Toast = autoclass('android.widget.Toast')
    ANDROID = True
except Exception:  # 桌面调试
    pass


def _c(v):
    """把 0xAARRGGBB 压成 Java int（有符号）。"""
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def _s(text):
    """把 Python 字符串转成 java.lang.String。

    pyjnius 只会自动转换声明为 String 的参数，对声明为 CharSequence 的
    参数不做转换——直接传 str 会抛 JavaException: No methods called
    setText ... matching your arguments。setText/setHint/Toast.makeText/
    ClipData.newPlainText 都吃 CharSequence，必须用这个包一层。
    """
    if not ANDROID:
        return text
    return JString(text)


def _argb(a, r, g, b):
    return _c((a << 24) | (r << 16) | (g << 8) | b)


if ANDROID:

    class _OnClickListener(PythonJavaClass):
        __javainterfaces__ = ['android/view/View$OnClickListener']
        __javacontext__ = 'app'

        def __init__(self, cb):
            super().__init__()
            self._cb = cb

        @java_method('(Landroid/view/View;)V')
        def onClick(self, view):
            try:
                self._cb()
            except Exception:
                traceback.print_exc()


    class _OnTouchListener(PythonJavaClass):
        """拖动 + 单击识别（位移小于阈值且按下时长短视为点击）。"""
        __javainterfaces__ = ['android/view/View$OnTouchListener']
        __javacontext__ = 'app'

        def __init__(self, on_tap=None, on_drag=None, on_down=None, on_up=None,
                     slop=10):
            super().__init__()
            self._on_tap = on_tap
            self._on_drag = on_drag
            self._on_down = on_down
            self._on_up = on_up
            self._slop = slop
            self._sx = self._sy = 0.0
            self._t0 = 0.0
            self._moved = False

        @java_method('(Landroid/view/View;Landroid/view/MotionEvent;)Z')
        def onTouch(self, view, event):
            try:
                a = event.getActionMasked()
                if a == MotionEvent.ACTION_DOWN:
                    self._sx = event.getRawX()
                    self._sy = event.getRawY()
                    self._t0 = time.time()
                    self._moved = False
                    if self._on_down:
                        self._on_down()
                elif a == MotionEvent.ACTION_MOVE:
                    dx = event.getRawX() - self._sx
                    dy = event.getRawY() - self._sy
                    if abs(dx) > self._slop or abs(dy) > self._slop:
                        self._moved = True
                    if self._moved and self._on_drag:
                        self._on_drag(dx, dy)
                elif a in (MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL):
                    if self._on_up:
                        self._on_up()
                    if a == MotionEvent.ACTION_UP and (not self._moved) \
                            and (time.time() - self._t0 < 0.6) and self._on_tap:
                        self._on_tap()
            except Exception:
                traceback.print_exc()
            return True


    #: Java 助手（hook.py 在构建时把它编译进 APK，见 java/ProjectionCallback.java）
    #: autoclass 用点号形式；__javainterfaces__ 用斜杠形式（jnius 的惯例）
    _CB_CLASS = 'com.fojiaoai.fojiaoaisearch.ProjectionCallback'
    _CB_LISTENER = 'com/fojiaoai/fojiaoaisearch/ProjectionCallback$Listener'

    class _StopListener(PythonJavaClass):
        """Java 助手 ProjectionCallback.Listener 的 Python 实现。

        为什么绕这一圈：Android 14+ 把 MediaProjection.Callback 从接口改成了
        抽象类，而 jnius 的 PythonJavaClass 内部用 java.lang.reflect.Proxy，
        只能实现接口、不能子类化抽象类 —— 所以 Python 侧直接实现它是做不到的
        （老代码 _PJCallback 把它当接口声明，构造时必然抛异常被吞掉）。
        Java 侧 ProjectionCallback 继承 Callback、把 onStop 转发到这个接口上，
        而接口 jnius 是能实现的。

        这个回调不是可选项：Android 14+ 要求 createVirtualDisplay() 之前必须
        注册回调，否则系统不往 ImageReader 送帧（表现为虚拟显示 state=ON、
        投影也持有，却一帧都收不到）。
        """
        __javainterfaces__ = [_CB_LISTENER]
        __javacontext__ = 'app'

        def __init__(self, on_stop):
            super().__init__()
            self._on_stop = on_stop

        @java_method('()V')
        def onProjectionStop(self):
            try:
                self._on_stop()
            except Exception:
                traceback.print_exc()


class AndroidBridge(object):
    """封装全部原生操作；所有 view 操作都通过 run_on_ui_thread 派发。"""

    REQUEST_CODE = 10086

    def __init__(self):
        self._wm = cast('android.view.WindowManager',
                        activity.getSystemService(Context.WINDOW_SERVICE))
        metrics = activity.getResources().getDisplayMetrics()
        self._density = metrics.density
        self._dpi = metrics.densityDpi
        self._ball = None
        self._ball_lp = None
        self._ball_base = (0, 0)
        self._panel = None
        self._panel_lp = None
        self._panel_base = (0, 0)
        self._answer_tv = None
        self._edit = None
        self._proxies = []          # 持有 Java 代理对象引用，防 GC
        self._projection = None
        self._reader = None         # 常驻的截屏 ImageReader
        self._vd = None             # 常驻的 VirtualDisplay
        self._reader_size = (0, 0)
        #: 本次授权是否已经尝试过建会话。Android 14+ 上一个 MediaProjection
        #: 只能成功调用一次 createVirtualDisplay()，所以绝不允许重试建第二个。
        self._session_attempted = False
        #: MediaProjection.Callback（Java 助手实例），在主线程造好后复用
        self._proj_cb = None
        self._mpm = None
        self._service_cls = None
        self._busy = False
        self._last_answer = ''
        self._cfg = {'tap_action': 'screenshot'}

    # ---------------- 基础工具 ----------------
    def _dp(self, v):
        return int(v * self._density + 0.5)

    def _screen(self):
        try:
            b = self._wm.getMaximumWindowMetrics().getBounds()
            return b.width(), b.height()
        except Exception:
            m = activity.getResources().getDisplayMetrics()
            return m.widthPixels, m.heightPixels

    @run_on_ui_thread
    def _toast_ui(self, msg):
        try:
            Toast.makeText(activity, _s(msg), Toast.LENGTH_SHORT).show()
        except Exception:
            traceback.print_exc()

    def toast(self, msg):
        self._toast_ui(msg)

    @run_on_ui_thread
    def _ui_call(self, fn):
        fn()

    # ---------------- 权限 ----------------
    def has_overlay_permission(self):
        return ASettings.canDrawOverlays(activity)

    def request_overlay_permission(self):
        self._open_overlay_settings()

    @run_on_ui_thread
    def _open_overlay_settings(self):
        if self.has_overlay_permission():
            self.toast('已有悬浮窗权限')
            return
        i = Intent(ASettings.ACTION_MANAGE_OVERLAY_PERMISSION,
                   Uri.parse('package:' + activity.getPackageName()))
        activity.startActivity(i)

    def has_projection(self):
        return self._projection is not None

    def request_projection(self):
        self._request_projection_ui()

    @run_on_ui_thread
    def _request_projection_ui(self):
        # p4a 的观察者模式： onActivityResult 转发到 python 回调
        self._mpm = cast('android.media.projection.MediaProjectionManager',
                         activity.getSystemService(Context.MEDIA_PROJECTION_SERVICE))
        # 必须先解绑：p4a 的 bind() 不去重，每次调用都会新建一个
        # ActivityResultListener 注册到 Java 侧。重复点「开启截屏授权」会累积
        # 监听器，导致一次授权结果触发 N 次 _on_activity_result、并发调用
        # getMediaProjection()。Android 14+ 上后建的投影会停掉前一个，
        # 竞争赋值下 self._projection 可能停在已失效的那个上 → 截屏拿不到帧。
        _android_activity.unbind(on_activity_result=self._on_activity_result)
        _android_activity.bind(on_activity_result=self._on_activity_result)
        # 回调对象在主线程造好（见 _make_projection_callback 的说明）
        self._make_projection_callback()
        activity.startActivityForResult(self._mpm.createScreenCaptureIntent(),
                                        self.REQUEST_CODE)

    def _on_activity_result(self, requestCode, resultCode, data):
        if requestCode != self.REQUEST_CODE:
            return
        if resultCode != -1 or data is None:
            self.toast('未授予截屏权限')
            return
        # 【主线程】解析服务类、启动服务、构造回调。
        # jnius 的类查找底层是 JNI FindClass，在 threading.Thread 这种
        # 附加线程上会用系统类加载器，看不到本应用的类（实测报
        # ClassNotFoundException: ...ServiceMedcap）。
        try:
            self._start_capture_service()
        except Exception:
            traceback.print_exc()
        # 【工作线程】前台服务是异步起来的（p4a 的服务要先启动一个
        # Python 解释器，实测 1 秒以上才调 startForeground），
        # 所以轮询重试直到就绪，再取 MediaProjection。
        # （回调对象已在 _request_projection_ui 里、于主线程造好。）
        threading.Thread(target=self._wait_projection,
                         args=(resultCode, data), daemon=True).start()

    #: p4a 按 buildozer.spec 里的 services = medcap:... 生成的 Java 服务类
    SERVICE_CLASS = 'com.fojiaoai.fojiaoaisearch.ServiceMedcap'

    def _start_capture_service(self):
        """启动 mediaProjection 类型的前台服务。

        Android 14 起，截屏必须由一个该类的前台服务承载，否则
        getMediaProjection() 抛 SecurityException：
        "Media projections require a foreground service of type
        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION"。
        """
        if self._service_cls is None:
            self._service_cls = autoclass(self.SERVICE_CLASS)
        self._service_cls.start(activity, '')
        return True

    def _wait_projection(self, resultCode, data):
        """等前台服务就绪后取 MediaProjection。

        p4a 的服务跑在独立进程里，要先启动一个 Python 解释器才调用
        startForeground()，实测要 1 秒以上。所以这里分次重试（间隔 1 秒、
        最多约 8 秒），等前台服务真正就绪再取。
        """
        from kivy.logger import Logger
        Logger.info('[fojiao] 等待前台服务就绪…')
        proj = None
        err = None
        for i in range(8):
            try:
                proj = self._mpm.getMediaProjection(resultCode, data)
                Logger.info('[fojiao] MediaProjection 获取成功（第 %d 次）'
                            % (i + 1))
                break
            except Exception as e:
                err = e
                Logger.info('[fojiao] 第 %d 次尚未就绪: %s' % (i + 1, e))
                time.sleep(1.0)
        if proj is None:
            Logger.error('[fojiao] 重试仍未成功: %s' % err)
            self._set_answer(
                '截屏授权没成功：%s\n\n'
                '可以再点一次「② 开启截屏授权」（前台服务此时已经在跑，'
                '第二次通常就过了）；\n'
                '或者改用面板上的【最新截图】：先用手机截图快捷键截屏，'
                '再点它读图搜题。' % err)
            return
        self._projection = proj
        # Android 14+ 必须先注册回调、再 createVirtualDisplay()，否则系统
        # 不往 ImageReader 送帧。两者都派发到主线程执行，先后有保证。
        self._register_projection_callback(proj)
        # 授权成功就把常驻截屏会话建好（一次就够，之后一直复用），
        # 并直接弹出悬浮球 —— 省掉"回 App 点③再切回来"这一圈。
        self._session_attempted = False
        self.start_capture_session()
        if self.has_overlay_permission():
            try:
                self.show_ball()
            except Exception:
                traceback.print_exc()
        self.toast('截屏授权成功，悬浮球已就绪，去刷题吧')

    def _make_projection_callback(self):
        """(主线程) 造一个 MediaProjection.Callback 实例。

        必须在这条主线程上造：jnius 解析"应用自己的类"要用应用类加载器，
        在 threading.Thread 这种附加线程上会走系统类加载器而找不到。
        """
        if self._proj_cb is not None:
            return self._proj_cb
        listener = _StopListener(self._on_projection_stopped)
        self._proxies.append(listener)
        helper = autoclass(_CB_CLASS)(listener)
        self._proxies.append(helper)
        self._proj_cb = helper
        return helper

    @run_on_ui_thread
    def _register_projection_callback(self, proj):
        try:
            if self._proj_cb is None:
                self._make_projection_callback()
            proj.registerCallback(self._proj_cb, None)
            from kivy.logger import Logger
            Logger.info('[fojiao] 已注册 MediaProjection 回调'
                        '（Android 14+ 缺它系统就不送帧）')
        except Exception:
            traceback.print_exc()

    def _on_projection_stopped(self):
        self._projection = None
        # 截屏会话是跟着投影走的：投影没了会话也作废，重建要等下次授权
        self._ui_call(self._release_capture_session)
        self._session_attempted = False
        self._proj_cb = None
        self._set_answer('注意：截屏授权已被系统回收（锁屏或切后台会触发），'
                         '请回到「佛脚AI搜题」重新点一次「② 开启截屏授权」。')

    # ---------------- 悬浮球 ----------------
    def set_cfg(self, cfg):
        self._cfg = cfg or {}

    def show_ball(self):
        self._show_ball_ui()

    @run_on_ui_thread
    def _show_ball_ui(self):
        if self._ball is not None:
            self._ball.setVisibility(View.VISIBLE)
            return
        sw, sh = self._screen()
        size = self._dp(56)
        b = Button(activity)
        gd = GradientDrawable()
        gd.setShape(GradientDrawable.OVAL)
        gd.setColor(_argb(235, 37, 99, 235))
        gd.setStroke(self._dp(2), _argb(255, 255, 255, 255))
        b.setBackground(gd)
        b.setText(_s('搜'))
        b.setTextColor(_argb(255, 255, 255, 255))
        b.setTextSize(20.0)
        lp = WMLP(size, size, WMLP.TYPE_APPLICATION_OVERLAY,
                  WMLP.FLAG_NOT_FOCUSABLE | WMLP.FLAG_NOT_TOUCH_MODAL,
                  PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP | Gravity.START
        lp.x = sw - size - self._dp(14)
        lp.y = int(sh * 0.55)

        def on_down():
            self._ball_base = (lp.x, lp.y)
            b.setAlpha(0.6)

        def on_up():
            b.setAlpha(1.0)

        def on_drag(dx, dy):
            lp.x = int(self._ball_base[0] + dx)
            lp.y = int(self._ball_base[1] + dy)
            try:
                self._wm.updateViewLayout(b, lp)
            except Exception:
                pass

        # 注意：pyjnius 的 PythonJavaClass 子类底层是 Cython __cinit__，
        # 不接受关键字参数，必须按位置传（参数顺序见类的 __init__）
        tl = _OnTouchListener(self.ball_tap_default, on_drag, on_down, on_up,
                              self._dp(6))
        self._proxies.append(tl)
        b.setOnTouchListener(tl)
        self._wm.addView(b, lp)
        self._ball = b
        self._ball_lp = lp

    def hide_ball(self):
        self._hide_ball_ui()

    @run_on_ui_thread
    def _hide_ball_ui(self):
        if self._ball is not None:
            try:
                self._wm.removeView(self._ball)
            except Exception:
                pass
            self._ball = None
            self._ball_lp = None

    @run_on_ui_thread
    def _ball_visible_ui(self, visible):
        if self._ball is not None:
            self._ball.setVisibility(View.VISIBLE if visible else View.INVISIBLE)

    @run_on_ui_thread
    def _panel_visible_ui(self, visible):
        # 截图时把面板也藏起来，避免被截进画面
        if self._panel is not None:
            self._panel.setVisibility(View.VISIBLE if visible else View.INVISIBLE)

    @run_on_ui_thread
    def move_task_to_back(self):
        activity.moveTaskToBack(True)

    # ---------------- 悬浮答案面板 ----------------
    def show_panel(self, text=''):
        self._show_panel_ui(text)

    @run_on_ui_thread
    def _show_panel_ui(self, text):
        if self._panel is None:
            self._build_panel()
        if self._panel is not None:
            self._panel.setVisibility(View.VISIBLE)
        if text and self._answer_tv is not None:
            self._answer_tv.setText(_s(text))

    def _build_panel(self):
        sw, sh = self._screen()
        pw, ph = int(sw * 0.88), int(sh * 0.52)

        root = LinearLayout(activity)
        root.setOrientation(LinearLayout.VERTICAL)
        bg = GradientDrawable()
        bg.setColor(_argb(243, 17, 24, 39))
        bg.setCornerRadius(float(self._dp(16)))
        root.setBackground(bg)
        root.setPadding(self._dp(14), self._dp(8), self._dp(14), self._dp(10))

        # 标题栏（拖动区）+ 最小化 + 关闭
        title = LinearLayout(activity)
        title.setOrientation(LinearLayout.HORIZONTAL)
        tv = TextView(activity)
        tv.setText(_s('AI 解题'))
        tv.setTextColor(_argb(255, 147, 197, 253))
        tv.setTextSize(15.0)
        title.addView(tv, LLLP(0, -2, 1.0))
        btn_min = self._small_btn('—')
        btn_close = self._small_btn('×')
        title.addView(btn_min, LLLP(-2, -2))
        title.addView(btn_close, LLLP(-2, -2))
        root.addView(title, LLLP(-1, -2))

        # 答案区
        sv = ScrollView(activity)
        sv.setFillViewport(True)
        ans = TextView(activity)
        ans.setTextColor(_argb(240, 229, 231, 235))
        ans.setTextSize(14.0)
        ans.setPadding(0, self._dp(8), 0, self._dp(8))
        sv.addView(ans)
        root.addView(sv, LLLP(-1, 0, 1.0))

        # 输入行：输入框 + 弹键盘 + 提问
        row = LinearLayout(activity)
        row.setOrientation(LinearLayout.HORIZONTAL)
        edit = EditText(activity)
        edit.setHint(_s('点这里输入/粘贴题目'))
        edit.setTextColor(_argb(255, 255, 255, 255))
        edit.setHintTextColor(_argb(170, 156, 163, 175))
        edit.setTextSize(13.0)
        edit.setMaxLines(3)
        btn_kbd = self._small_btn('键盘')
        btn_send = self._small_btn('问')
        row.addView(edit, LLLP(0, -2, 1.0))
        row.addView(btn_kbd, LLLP(-2, -2))
        row.addView(btn_send, LLLP(-2, -2))
        root.addView(row, LLLP(-1, -2))

        # 功能按钮行
        row2 = LinearLayout(activity)
        row2.setOrientation(LinearLayout.HORIZONTAL)
        btn_clip = self._small_btn('读剪贴板')
        btn_shot = self._small_btn('截图搜题')
        btn_latest = self._small_btn('最新截图')
        btn_copy = self._small_btn('复制答案')
        for b in (btn_clip, btn_shot, btn_latest, btn_copy):
            row2.addView(b, LLLP(0, -2, 1.0))
        root.addView(row2, LLLP(-1, -2))

        # 不设 FLAG_NOT_FOCUSABLE：面板必须能拿到输入焦点，
        # EditText 才能正常编辑、也才能读剪贴板（Android 10+ 只允许获焦应用读）。
        # 保留 FLAG_NOT_TOUCH_MODAL，面板外的触摸照常传给底下的刷题 App。
        lp = WMLP(pw, ph, WMLP.TYPE_APPLICATION_OVERLAY,
                  WMLP.FLAG_NOT_TOUCH_MODAL,
                  PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP | Gravity.START
        lp.x = (sw - pw) // 2
        lp.y = int(sh * 0.14)
        lp.softInputMode = WMLP.SOFT_INPUT_ADJUST_RESIZE

        # 标题栏拖动整个面板
        def on_down():
            self._panel_base = (lp.x, lp.y)

        def on_drag(dx, dy):
            lp.x = int(self._panel_base[0] + dx)
            lp.y = int(self._panel_base[1] + dy)
            try:
                self._wm.updateViewLayout(root, lp)
            except Exception:
                pass

        # 同样必须用位置参数：(on_tap, on_drag, on_down, on_up, slop)
        tl = _OnTouchListener(None, on_drag, on_down, None, self._dp(8))
        self._proxies.append(tl)
        title.setOnTouchListener(tl)

        def _show_keyboard():
            # 弹软键盘并让面板避让出输入框位置
            edit.requestFocus()
            try:
                lp.softInputMode = (WMLP.SOFT_INPUT_ADJUST_RESIZE
                                    | WMLP.SOFT_INPUT_STATE_VISIBLE)
                self._wm.updateViewLayout(root, lp)
            except Exception:
                pass
            imm = cast('android.view.inputmethod.InputMethodManager',
                       activity.getSystemService(Context.INPUT_METHOD_SERVICE))
            imm.showSoftInput(edit, 0)

        def _close():
            try:
                self._wm.removeView(root)
            except Exception:
                pass
            self._panel = None
            self._panel_lp = None
            self._answer_tv = None
            self._edit = None

        def _read_clip():
            # 面板窗口可获焦，点这个按钮的瞬间焦点已在面板上，
            # 因此能读到剪贴板；0.25s 只是留一点余量
            def worker():
                time.sleep(0.25)
                text = self.get_clipboard().strip()
                if not text:
                    self._set_answer('剪贴板为空或读不到（Android 10+ 只允许'
                                     '获焦应用读剪贴板）。\n\n可长按输入框手动'
                                     '粘贴，或直接用「截图搜题」。')
                    return
                self._ui_call(lambda: edit.setText(_s(text)))
                self.solve_text_async(text)
            threading.Thread(target=worker, daemon=True).start()

        def _send():
            q = str(edit.getText().toString()).strip()
            if not q:
                self._set_answer('请先在输入框里输入或粘贴题目，'
                                 '再点「问」。')
                return
            self.solve_text_async(q)

        def _copy():
            if self._last_answer:
                self.set_clipboard(self._last_answer)
                self.toast('答案已复制到剪贴板')
            else:
                self.toast('还没有可复制的答案')

        for btn, cb in ((btn_min, lambda: root.setVisibility(View.GONE)),
                        (btn_close, _close),
                        (btn_kbd, _show_keyboard),
                        (btn_send, _send),
                        (btn_clip, _read_clip),
                        (btn_shot, self.capture_and_solve),
                        (btn_latest, self.solve_from_latest_shot),
                        (btn_copy, _copy)):
            p = _OnClickListener(cb)
            self._proxies.append(p)
            btn.setOnClickListener(p)

        self._wm.addView(root, lp)
        self._panel = root
        self._panel_lp = lp
        self._answer_tv = ans
        self._edit = edit

    def _small_btn(self, text):
        b = Button(activity)
        b.setText(_s(text))
        b.setTextColor(_argb(255, 147, 197, 253))
        b.setTextSize(12.0)
        b.setPadding(self._dp(8), self._dp(2), self._dp(8), self._dp(2))
        return b

    def _set_answer(self, text):
        if self._panel is None:
            self.show_panel(text)
            return
        self._set_answer_ui(text)

    @run_on_ui_thread
    def _set_answer_ui(self, text):
        if self._answer_tv is not None:
            self._answer_tv.setText(_s(text))

    # ---------------- 剪贴板 ----------------
    def get_clipboard(self):
        try:
            cm = cast('android.content.ClipboardManager',
                      activity.getSystemService(Context.CLIPBOARD_SERVICE))
            cd = cm.getPrimaryClip()
            if cd is None or cd.getItemCount() == 0:
                return ''
            return str(cd.getItemAt(0).coerceToText(activity).toString())
        except Exception:
            return ''

    def set_clipboard(self, text):
        def do():
            try:
                cm = cast('android.content.ClipboardManager',
                          activity.getSystemService(Context.CLIPBOARD_SERVICE))
                ClipData = autoclass('android.content.ClipData')
                cm.setPrimaryClip(ClipData.newPlainText(_s('answer'), _s(text)))
            except Exception:
                traceback.print_exc()
        self._ui_call(do)

    # ---------------- 读最新截图（不依赖 MediaProjection）----------------
    #: 各家 ROM 存放截图的目录；专放截图的目录直接取最新图片，
    #: 混合目录（DCIM）只认文件名带 screenshot/截屏/截图的
    SHOT_DIRS = (
        ('/sdcard/Pictures/Screenshots', False),
        ('/sdcard/DCIM/Screenshots', False),
        ('/sdcard/Pictures/screenshots', False),
        ('/sdcard/Screenshots', False),
        ('/sdcard/Pictures/截屏', False),
        ('/sdcard/DCIM', True),
        ('/sdcard/Pictures', True),
    )
    _SHOT_KEYS = ('screenshot', 'screen_shot', '截屏', '截图')

    def _latest_shot_file(self):
        """找出最新的一张截图（文件系统方式，不需要截屏授权）。"""
        newest = None
        for d, need_key in self.SHOT_DIRS:
            try:
                if not os.path.isdir(d):
                    continue
                for name in os.listdir(d):
                    low = name.lower()
                    if not low.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        continue
                    if need_key and not any(k in low or k in name
                                           for k in self._SHOT_KEYS):
                        continue
                    p = os.path.join(d, name)
                    try:
                        m = os.path.getmtime(p)
                    except Exception:
                        continue
                    if newest is None or m > newest[0]:
                        newest = (m, p)
            except Exception:
                traceback.print_exc()
        return newest[1] if newest else None

    def ensure_media_permission(self):
        """请求读取图片的权限（Android 13+ 为 READ_MEDIA_IMAGES）。"""
        try:
            from android.permissions import request_permissions, Permission
        except Exception:
            traceback.print_exc()
            return
        perms = []
        for attr in ('READ_MEDIA_IMAGES', 'READ_EXTERNAL_STORAGE'):
            try:
                perms.append(getattr(Permission, attr))
            except Exception:
                pass
        if not perms:
            return
        try:
            request_permissions(perms)
        except Exception:
            traceback.print_exc()

    def _prepare_image(self, src_path, dst_path, max_w=1280):
        """解码并缩放到 max_w 宽，另存 JPEG（原图可能很大，省流量与token）。"""
        bmp = BitmapFactory.decodeFile(src_path)
        if bmp is None:
            raise RuntimeError('无法读取图片（可能没有相册权限）：' + src_path)
        w, h = bmp.getWidth(), bmp.getHeight()
        if w > max_w:
            bmp = Bitmap.createScaledBitmap(bmp, max_w,
                                            int(h * max_w / float(w)), True)
        out = FileOutputStream(dst_path)
        bmp.compress(BitmapFormat.JPEG, 88, out)
        out.close()
        return dst_path

    def solve_from_latest_shot(self):
        """读相册里最新的一张截图去搜题。"""
        if self._busy:
            self.toast('正在处理上一题，请稍候…')
            return
        self._busy = True
        self.show_panel('正在查找最新截图…')
        threading.Thread(target=self._latest_shot_worker, daemon=True).start()

    def _latest_shot_worker(self):
        try:
            src = None
            try:
                src = self._latest_shot_file()
            except Exception:
                traceback.print_exc()
            if not src:
                # 没找到：多半是首次使用缺权限，请求一次并提示。
                # 必须走主线程：p4a 的 request_permissions 内部会
                # autoclass('...PythonActivity$PermissionsCallback')，在
                # threading.Thread 上走的是系统类加载器 → ClassNotFoundException，
                # 弹窗根本不会出现（实测报 DexPathList[[directory "."]]）。
                self._ui_call(self.ensure_media_permission)
                self._set_answer(
                    '没找到截图。\n\n'
                    '用法：先按手机的截图快捷键（一般是电源键+音量下，'
                    '或三指下滑）把题目截下来，再点【最新截图】。\n\n'
                    '如果这是第一次用：请在系统弹窗里允许读取图片，'
                    '然后重新点一次。')
                return
            # jnius 对象的 str() 是对象表示，不是路径，必须取绝对路径
            dst = os.path.join(activity.getCacheDir().getAbsolutePath(),
                               'latest_shot.jpg')
            self._prepare_image(src, dst)
            self._set_answer('已读到截图，正在识别题目…')
            q = ai_core.ocr_question(dst)
            self._set_answer('已识别题目，正在解答…')
            answer = ai_core.solve_question(q)
            self._last_answer = answer
            # 同截图搜题：题目不用重复贴，只回答案
            self._set_answer('【解答】\n' + ai_core.plain_text(answer))
        except Exception as e:
            traceback.print_exc()
            self._set_answer('错误：' + str(e))
        finally:
            self._busy = False

    # ---------------- 搜题主流程 ----------------
    def ball_tap_default(self):
        action = self._cfg.get('tap_action', 'screenshot')
        if action == 'clipboard':
            text = self.get_clipboard().strip()
            if not text:
                self.show_panel(
                    '没有读到剪贴板文字。\n\n'
                    'Android 10+ 只允许当前获焦的应用读剪贴板。\n'
                    '方法1：在题目界面长按选中题目文字 → 复制，'
                    '再点面板里的【读剪贴板】；\n'
                    '方法2：回 App 在设置里把点按动作改成「截图搜题」或'
                    '「读最新截图」。')
                return
            self.solve_text_async(text)
        elif action == 'shot':
            self.solve_from_latest_shot()
        else:
            self.capture_and_solve()

    def solve_text_async(self, question):
        if self._busy:
            self.toast('正在处理上一题，请稍候…')
            return
        self._busy = True
        self.show_panel('正在解答…\n\n【题目】\n' + ai_core.font_safe(question[:400]))

        def worker():
            try:
                answer = ai_core.solve_question(question)
                self._last_answer = answer
                self._set_answer('【题目】\n' + ai_core.font_safe(question) +
                                 '\n\n【解答】\n' +
                                 ai_core.plain_text(answer))
            except Exception as e:
                traceback.print_exc()
                self._set_answer('错误：' + str(e))
            finally:
                self._busy = False
        threading.Thread(target=worker, daemon=True).start()

    def capture_and_solve(self):
        if self._busy:
            self.toast('正在处理上一题，请稍候…')
            return
        if self._projection is None:
            # 注意：App 在后台时系统会拦截 Toast（实测 Android 15 会打日志
            # "Suppressing toast from package ... by user request"），
            # 用户什么也看不到。所以这里改用悬浮面板给反馈。
            self.show_panel('还没有截屏授权。\n\n'
                            'Android 14 起系统要求截屏必须由前台服务承载，'
                            '授权可能失败。\n'
                            '可以改用面板上的【最新截图】：先按手机截图快捷键'
                            '截屏，再点它读取最新截图搜题。\n\n'
                            '要尝试截屏授权：回到「佛脚AI搜题」点'
                            '「② 开启截屏授权」。')
            return
        self._busy = True
        self.show_panel('正在截图…')
        self._ball_visible_ui(False)
        self._panel_visible_ui(False)
        # jnius 对象的 str() 是对象表示，不是路径，必须取绝对路径
        path = os.path.join(activity.getCacheDir().getAbsolutePath(),
                            'fojiao_shot.jpg')

        def worker():
            try:
                self._do_capture(path)
                self._set_answer('正在识别题目…')
                q = ai_core.ocr_question(path)
                self._set_answer('已识别题目，正在解答…')
                answer = ai_core.solve_question(q)
                self._last_answer = answer
                # 题目就在屏幕上看着，面板又小，只回答案不重复贴题目
                self._set_answer('【解答】\n' + ai_core.plain_text(answer))
            except Exception as e:
                traceback.print_exc()
                self._set_answer('错误：' + str(e))
            finally:
                self._busy = False
                self._ball_visible_ui(True)
                self._panel_visible_ui(True)
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- MediaProjection 截屏 ----------------
    def _release_capture_session(self):
        """(主线程) 关掉常驻的 reader / virtual display。"""
        for attr in ('_vd', '_reader'):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                try:
                    obj.release()
                except Exception:
                    pass
            setattr(self, attr, None)
        self._reader_size = (0, 0)

    def _create_capture_session(self):
        """(必须主线程) 建常驻的 ImageReader + VirtualDisplay。

        **每个 MediaProjection 只能建一次，建成后一直复用、绝不重建。**
        Android 14+ 上 createVirtualDisplay() 对同一个投影实例只允许成功
        调用一次；关掉再建是无效的 —— 实测反复重建的后果就是"前几次一帧
        都收不到、某次偶然拿到帧但那是刚建会话时的黑帧"。
        """
        if self._session_attempted:
            return
        self._session_attempted = True
        self._release_capture_session()
        sw, sh = self._screen()
        self._reader = ImageReader.newInstance(sw, sh, PixelFormat.RGBA_8888, 2)
        self._vd = self._projection.createVirtualDisplay(
            'fojiao-cap', sw, sh, self._dpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            self._reader.getSurface(), None, None)
        self._reader_size = (sw, sh)
        from kivy.logger import Logger
        Logger.info('[fojiao] 截屏会话已建立 %dx%d（此后一直复用）' % (sw, sh))

    @run_on_ui_thread
    def start_capture_session(self):
        try:
            self._create_capture_session()
        except Exception:
            traceback.print_exc()

    def _do_capture(self, path):
        result = {}
        done = threading.Event()

        @run_on_ui_thread
        def start():
            try:
                # 会话只在授权时建一次（见 _create_capture_session）；
                # 这里只兜底，且绝不重试建第二个。
                if self._reader is None and not self._session_attempted:
                    self._create_capture_session()
                reader = self._reader
                sw, sh = self._reader_size
                if reader is None:
                    result['err'] = '截屏会话没建起来，请重新点「② 开启截屏授权」'
                    done.set()
                    return

                def read():
                    try:
                        # 等"隐藏悬浮球"进入新帧，并把积压的旧帧全部丢掉、
                        # 只留最后一帧 —— 否则可能截到球，或截到刚建会话时的黑帧
                        time.sleep(0.6)
                        img = None
                        t0 = time.time()
                        while time.time() - t0 < 0.6:
                            nxt = reader.acquireLatestImage()
                            if nxt is None:
                                time.sleep(0.05)
                                continue
                            if img is not None:
                                img.close()
                            img = nxt
                        # 一帧都还没有（会话刚建好时正常），再多等一会儿
                        if img is None:
                            t0 = time.time()
                            while img is None and time.time() - t0 < 5.0:
                                time.sleep(0.1)
                                img = reader.acquireLatestImage()
                        if img is None:
                            raise RuntimeError(
                                '没收到屏幕画面（虚拟显示 %dx%d）。'
                                '截屏会话可能已被系统回收，'
                                '请重新点「② 开启截屏授权」' % (sw, sh))
                        plane = img.getPlanes()[0]
                        row = plane.getRowStride()
                        pix = plane.getPixelStride()
                        buf = plane.getBuffer()
                        if row == sw * 4 and pix == 4:
                            bmp = Bitmap.createBitmap(sw, sh, BitmapConfig.ARGB_8888)
                            bmp.copyPixelsFromBuffer(buf)
                        else:  # 行对齐填充：先按 stride 建图再裁剪
                            full = Bitmap.createBitmap(row // 4, sh,
                                                       BitmapConfig.ARGB_8888)
                            full.copyPixelsFromBuffer(buf)
                            bmp = Bitmap.createBitmap(full, 0, 0, sw, sh)
                        img.close()
                        if bmp.getWidth() > 1280:
                            bmp = Bitmap.createScaledBitmap(
                                bmp, 1280,
                                int(bmp.getHeight() * 1280 / bmp.getWidth()),
                                True)
                        out = FileOutputStream(path)
                        bmp.compress(BitmapFormat.JPEG, 88, out)
                        out.close()
                        result['ok'] = True
                    except Exception as e:
                        result['err'] = str(e)
                    finally:
                        # 这里不能关 reader/vd —— 会话要留给下一次截图用
                        done.set()
                threading.Thread(target=read, daemon=True).start()
            except Exception as e:
                result['err'] = str(e)
                done.set()

        start()
        done.wait(15)
        if result.get('ok'):
            return True
        # 千万别在这里关掉会话：Android 14+ 上一个 MediaProjection 只能成功
        # 建一次 VirtualDisplay，关掉就再也建不回来（实测反复重建的结果是
        # 一帧都收不到）。会话留给下一次截图继续用。
        raise RuntimeError('截图失败：' + result.get('err', '超时'))


bridge = AndroidBridge() if ANDROID else None

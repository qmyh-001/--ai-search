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
    from android.runnable import Runnable as _UiRunnable
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
    FrameLayout = autoclass('android.widget.FrameLayout')
    FrameLayoutLP = autoclass('android.widget.FrameLayout$LayoutParams')
    LinearInterpolator = autoclass('android.view.animation.LinearInterpolator')
    DecelerateInterpolator = autoclass(
        'android.view.animation.DecelerateInterpolator')
    ProgressBar = autoclass('android.widget.ProgressBar')
    Typeface = autoclass('android.graphics.Typeface')
    RotateAnimation = autoclass('android.view.animation.RotateAnimation')
    Anim = autoclass('android.view.animation.Animation')
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


def _recycle(bmp):
    """立刻回收一张 Bitmap。

    截屏链路上每张全屏 ARGB_8888（1080x2400）约 10MB，而 App 没开
    largeHeap，全交给 GC 的话连续搜题十几轮就会把 Java 堆顶到 OOM
    （表现为先卡顿、后闪退）。这些图都是本地建了马上用完、从不交给
    View/Drawable，所以可以安全地显式回收。
    """
    if bmp is None:
        return
    try:
        if not bmp.isRecycled():
            bmp.recycle()
    except Exception:
        traceback.print_exc()


# ---------------- iOS 亮色配色（与 main.py 里的界面配色保持一致）----------------
C_BLUE = 0xFF0A84FF
C_BLUE_DARK = 0xFF0060DF
C_BLUE_TINT = 0xFFE9F3FF
C_GRAY_FILL = 0xFFF2F2F7
C_GRAY_FILL2 = 0xFFE5E5EA
C_TITLE = 0xFF1C1C1E
C_BODY = 0xFF3A3A3C
C_GRAY = 0xFF8E8E93
C_SEP = 0xFFE5E5EA

#: 原生控件里要用到的 Lucide 图标码位（字体随包，见 main.py 的 ICON）
_ICON_GLYPH = {
    'search': '\ue151',
    'resize': '\ue1c5',      # move-diagonal-2（↙↗）：拖动改大小
}

#: 画面全黑时的提示。真机上实测有两种成因，按常见程度排：
#: ① 授权时选的是"共享一个应用"，那个应用不在前台时截到的就是空画面；
#: ② 目标应用自己禁止截屏（FLAG_SECURE / 系统隐私保护）。
BLACK_FRAME_HINT = (
    '截到的画面是空的（全黑），两个常见原因：\n\n'
    '① 授权时选的是「共享一个应用」\n'
    '这种模式下只有那个应用在前台时才截得到画面。请在要搜题的'
    'App 界面里点悬浮球；或者重新授权时改选「共享整个屏幕」。\n\n'
    '② 该应用禁止被截屏\n'
    '有些应用会开启系统隐私保护（银行、支付类常见），'
    '任何截屏方式都拿不到画面。\n\n'
    '这两种情况都可以改用【读剪贴板】（在题目界面长按选中题目文字 → '
    '复制，再回到这个面板点【读剪贴板】）；如果那个界面连复制都不让用，'
    '就直接把题目打字或粘贴到面板的输入框里，点【问】。'
)

#: 面板第一次打开时的引导语（顺便把"隐私应用"和"能追问"说清楚）
PANEL_INTRO = (
    '点【截图搜题】自动截屏识别。\n\n'
    '· 出答案后，在下面输入框里接着问（比如"为什么选 B"），'
    '会带着这道题继续回答\n'
    '· 也可以直接把题目打字/粘贴进输入框，点【问】\n'
    '· 截到的画面全黑时，多半是授权选了「共享一个应用」——'
    '在要搜题的 App 界面里点悬浮球，或者重新授权选「共享整个屏幕」'
)


def _stateful(normal, pressed):
    """带按下反馈的背景（StateListDrawable）；失败就退回静态背景。"""
    try:
        sld = autoclass('android.graphics.drawable.StateListDrawable')()
        sld.addState([autoclass('android.R$attr').state_pressed], pressed)
        sld.addState([], normal)
        return sld
    except Exception:
        return normal


def _rounded(color, radius, stroke_w=0, stroke_color=0):
    """圆角矩形背景（Android GradientDrawable）。"""
    gd = GradientDrawable()
    gd.setShape(GradientDrawable.RECTANGLE)
    gd.setColor(_c(color))
    gd.setCornerRadius(float(radius))
    if stroke_w:
        gd.setStroke(int(stroke_w), _c(stroke_color))
    return gd


def _oval(color, stroke_w=0, stroke_color=0):
    """圆形背景。"""
    gd = GradientDrawable()
    gd.setShape(GradientDrawable.OVAL)
    gd.setColor(_c(color))
    if stroke_w:
        gd.setStroke(int(stroke_w), _c(stroke_color))
    return gd


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

        为什么绕这一圈：MediaProjection.Callback 在 API 33 中本来就是抽象类，
        而 jnius 的 PythonJavaClass 内部用 java.lang.reflect.Proxy，只能实现接口、
        不能子类化抽象类 —— 所以 Python 侧直接实现它在任何 Android 版本上都
        做不到（老代码 _PJCallback 把它当接口声明，构造时必然抛异常被吞掉）。
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
        #: 状态行（加载指示器 + "正在…"文案）
        self._status_row = None
        self._status_tv = None
        #: 答案区的纯文本（Python 侧持有，追加/替换都基于它，避免来回取 Java 字符串）
        self._panel_text = ''
        self._answer_sv = None
        #: 用户拖出来的面板尺寸（本进程内记住）；None = 用默认尺寸
        self._panel_size = None
        self._resize_base = None
        #: 图标字体 / 加载动画引用
        self._typeface = None
        #: lucide.ttf 是否真的加载成功（缺字体时要退回汉字，见 _icon_glyph）
        self._icon_font_ok = False
        self._loader_anim = None
        self._loader_view = None
        #: 必须长期活着的 Java 代理（目前只有 MediaProjection 回调）。
        #: pyjnius 的 PythonJavaClass 不受 Java 侧引用计数保护：上游
        #: jnius_proxy.pxi 里传给 Java 的是裸 PyObject* 指针，Python 一侧的
        #: 强引用是它唯一的存在依据。所以只要 Java 侧还可能回调，就得留住，
        #: 否则回调打到已释放的对象上就是 native 崩溃（Python 看不到 traceback）。
        self._proxies = []
        #: 面板的代理：绑在面板控件上，闭包捕获了 root/edit/lp，所以它们
        #: 同时钉住整棵面板视图树。必须随面板一起释放（关闭时先解绑再清空），
        #: 否则每关一次面板就永久留下一棵树（实测每轮 +28 个 View）。
        self._panel_proxies = []
        #: 悬浮球的代理：同上，随球一起释放
        self._ball_proxies = []
        self._projection = None
        self._reader = None         # 常驻的截屏 ImageReader
        self._vd = None             # 常驻的 VirtualDisplay
        self._reader_size = (0, 0)
        #: 保护 _busy 与下面三个读帧状态的小锁。
        #: 读帧与释放会话必须互斥：read() 线程可能正卡在
        #: acquireLatestImage()/getPlanes() 上，这时 close(reader) 轻则抛
        #: IllegalStateException，重则 native 崩溃 —— 所以正在读帧时只打
        #: "失效"标记，等 read() 收尾后再真正释放。
        self._lock = threading.Lock()
        self._capturing = False
        self._session_dead = False
        #: 读帧代数：只有"当前这一代"的读线程能清 _capturing（15s 超时后
        #: 旧线程可能还在跑，它不能把新线程的状态清掉）
        self._cap_gen = 0
        #: 本次授权是否已经尝试过建会话。Android 14+ 上一个 MediaProjection
        #: 只能成功调用一次 createVirtualDisplay()，所以绝不允许重试建第二个。
        self._session_attempted = False
        #: 当前 MediaProjection.Callback（每次授权单独创建）
        self._proj_cb = None
        #: Java 助手类必须在主线程解析；解析后可以复用 Class 引用
        self._projection_callback_cls = None
        #: 正在等一次新授权（此时旧投影被回收是正常现象，不该弹提示）
        self._auth_in_progress = False
        #: 本进程里是否建过截屏会话（换授权时用它决定要不要等旧显示拆完）
        self._had_session = False
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

    def _toast_ui(self, msg):
        """(UI 线程) 弹 Toast。"""
        try:
            Toast.makeText(activity, _s(msg), Toast.LENGTH_SHORT).show()
        except Exception:
            traceback.print_exc()

    def toast(self, msg):
        self._ui_call(lambda: self._toast_ui(msg))

    def _ui_call(self, fn):
        """把 fn 丢到 UI 线程执行。

        不能用 @run_on_ui_thread 包一层再转发：p4a 那个装饰器对同一个函数
        只缓存一个 Runnable 实例，而 Runnable.__call__ 是
        `self.args = args` 的覆盖式赋值（见
        _p4a_src/v2024.01.21/.../android/runnable.py:29-33 与 50-53）——
        前一条消息还没执行、后一条就把它覆盖掉，UI 线程会把后一个 fn 执行
        两遍、前一个永远丢掉。实测丢过 _release_capture_session，截屏会话
        因此永不释放。每次新建 Runnable 就没这个问题；p4a 自己会用
        Runnable.__runnables__ 把实例撑到执行完为止。

        返回 True/False 表示"有没有成功派发出去"。调用方如果是在等一个
        事件（比如 _do_capture 的 done），必须看这个返回值：派发失败时
        没人会去 set 那个事件，干等只会等到超时和一条误导性的错误。
        """
        if not ANDROID:
            fn()
            return True
        try:
            _UiRunnable(fn)()
            return True
        except Exception:
            traceback.print_exc()
            return False

    def _claim_busy(self):
        """抢"正在处理"标记，抢到返回 True（连点时不放两路并发跑）。"""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            return True

    def _release_busy(self):
        with self._lock:
            self._busy = False

    @staticmethod
    def _unbind(view, kind='click'):
        """把控件上的监听器摘掉（关面板/收悬浮球时用）。

        必须做：那些代理的闭包捕获了整棵面板树，而 pyjnius 不保证 Java 侧
        持有代理期间 Python 对象不被回收，所以要"先解绑、再丢引用"。
        jnius 对接口参数传 None 会命中重载并置成 null jobject
        （jnius_utils.pxi 的 calculate_score 里 `if arg is None: score += 10`、
        jnius_conversion.pxi 的 populate_args 里 `if py_arg is None:
        j_args[index].l = NULL`），不需要 cast。
        """
        if view is None:
            return
        try:
            if kind == 'touch':
                view.setOnTouchListener(None)
            else:
                view.setOnClickListener(None)
        except Exception:
            traceback.print_exc()

    def _drop_panel_refs(self):
        """(UI 线程) 丢掉面板相关的一切强引用 —— 这一步才真正让视图树可回收。

        调用前必须先 removeView + 解绑监听（见 _build_panel 里的 _close）。
        这里会清空 _panel_proxies，而"正在执行的那个回调代理"由当前调用栈
        持有，所以从回调内部调用是安全的；但顺序绝不能颠倒成
        "先清引用、再解绑"（那样 Java 侧回调会打到已释放的对象上）。
        """
        if self._loader_anim is not None:
            try:
                self._loader_anim.cancel()   # setRepeatCount(-1)：不取消就永不结束
            except Exception:
                pass
            self._loader_anim = None
        if self._loader_view is not None:
            try:
                self._loader_view.clearAnimation()
            except Exception:
                pass
            self._loader_view = None
        self._panel = None
        self._panel_lp = None
        self._answer_tv = None
        self._edit = None
        self._status_row = None
        self._status_tv = None
        self._answer_sv = None
        self._panel_text = ''
        self._panel_proxies = []

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
        # 回调对象不在这里造：它绑在"某一个具体投影"上，必须等拿到
        # MediaProjection 之后再造（见 _register_projection_callback）。
        # 造在这里还有更坏的后果 —— autoclass 失败会抛异常，把下面这行
        # startActivityForResult 整个吞掉，用户点「②」时系统对话框不弹、
        # 界面上也没有任何提示。
        activity.startActivityForResult(self._mpm.createScreenCaptureIntent(),
                                        self.REQUEST_CODE)

    def _on_activity_result(self, requestCode, resultCode, data):
        if requestCode != self.REQUEST_CODE:
            return
        if resultCode != -1 or data is None:
            self.toast('未授予截屏权限')
            return
        # 【主线程】解析服务类并启动前台服务。
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
        self._auth_in_progress = True
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
            self._auth_in_progress = False
            Logger.error('[fojiao] 重试仍未成功: %s' % err)
            self._set_answer(
                '截屏授权没成功：%s\n\n'
                '可以再点一次「② 开启截屏授权」（前台服务此时已经在跑，'
                '第二次通常就过了）；\n'
                '或者改用面板上的【最新截图】：先用手机截图快捷键截屏，'
                '再点它读图搜题。' % err)
            return
        self._projection = proj
        # 标记位先清空：只有本次回调注册成功，才允许建立截屏会话
        # （没有回调 = 系统不送帧，而一个投影只能建一次 VirtualDisplay，
        #   白建一次就等于这一轮授权彻底废掉）。
        self._proj_cb = None
        # Android 14+ 必须先注册回调、再 createVirtualDisplay()，否则系统
        # 不往 ImageReader 送帧。两者都是派发到主线程执行的普通调用，
        # 同一个 UI 线程队列保证"先注册、后建会话"的顺序。
        self._register_projection_callback(proj)
        # 授权成功就把常驻截屏会话建好（一次就够，之后一直复用），
        # 并直接弹出悬浮球 —— 省掉"回 App 点③再切回来"这一圈。
        self._session_attempted = False
        # 换授权时要等一下：旧投影的虚拟显示是系统在 onStop 之后异步拆掉的
        # （实测"释放旧显示"和"新建显示"只差 2ms），紧挨着立刻重建的话，
        # 新显示 state=ON 却一帧都不送。这里在**工作线程**上等（不能在
        # 主线程 sleep，会卡住 UI）。
        # 用 _had_session 判断而不是看 _reader/_vd 是否还在：那两个引用会被
        # onStop 的异步释放清掉，读它等于在赌时序，有时会漏掉这次等待。
        if self._had_session:
            Logger.info('[fojiao] 换授权：等旧截屏显示拆干净再重建…')
            time.sleep(1.5)
        self.start_capture_session()
        self._auth_in_progress = False
        if self.has_overlay_permission():
            try:
                self.show_ball()
            except Exception:
                traceback.print_exc()
        self.toast('截屏授权成功，悬浮球已就绪，去刷题吧')

    @run_on_ui_thread
    def _register_projection_callback(self, proj):
        """(主线程) 为"这个具体投影"创建并注册回调。

        回调必须一个投影一个：系统回收旧投影时也会回调它的 onStop，若多个
        投影共用一个回调对象、又无条件清理状态，就会把刚授权成功的新投影
        连带作废（重复点「②」会变成"用一次废一次"）。所以这里把 proj 绑进
        闭包，由 _on_projection_stopped 比对实例。

        必须在这条主线程上造：jnius 解析"应用自己的类"要用应用类加载器，
        在 threading.Thread 这种附加线程上会走系统类加载器而找不到。
        """
        try:
            if self._projection_callback_cls is None:
                self._projection_callback_cls = autoclass(_CB_CLASS)
            listener = _StopListener(lambda: self._on_projection_stopped(proj))
            # 持有 Java 代理引用防 GC（旧回调也要留住：Java 侧仍可能回调它）
            self._proxies.append(listener)
            helper = self._projection_callback_cls(listener)
            self._proxies.append(helper)
            proj.registerCallback(helper, None)
            self._proj_cb = helper
            from kivy.logger import Logger
            Logger.info('[fojiao] 已注册 MediaProjection 回调'
                        '（Android 14+ 缺它系统就不送帧）')
        except Exception:
            traceback.print_exc()
            from kivy.logger import Logger
            Logger.error('[fojiao] MediaProjection 回调注册失败，本次无法截屏')

    def _on_projection_stopped(self, proj=None):
        """MediaProjection 被系统回收（锁屏/切后台/被新授权顶掉）。

        只有"当前投影"真的没了才清会话；旧投影的回调不能影响新会话。
        """
        if proj is not None and self._projection is not None \
                and proj is not self._projection:
            from kivy.logger import Logger
            Logger.info('[fojiao] 旧投影被系统回收（当前会话不受影响）')
            return
        self._projection = None
        # 截屏会话是跟着投影走的：投影没了会话也作废，重建要等下次授权
        self._ui_call(self._release_capture_session)
        self._session_attempted = False
        self._proj_cb = None
        if self._auth_in_progress:
            # 这是"换授权"时旧投影被系统顶掉，属于正常流程，
            # 不要弹"授权已被回收"吓用户（新会话马上会建好）
            return
        self._set_answer('注意：截屏授权已被系统回收（锁屏或切后台会触发），'
                         '请回到「佛脚AI搜题」重新点一次「② 开启截屏授权」。')

    # ---------------- 悬浮球 ----------------
    def set_cfg(self, cfg):
        self._cfg = cfg or {}

    def show_ball(self):
        self._show_ball_ui()

    @run_on_ui_thread
    def _show_ball_ui(self):
        """iOS 风的悬浮球：蓝色圆 + 白色图标 + 一圈柔和投影。

        窗口比球本身大一圈（多的部分放投影），所以拖动时视觉上会有一点点
        偏移，这是为了让投影有地方画（悬浮窗内部没有系统阴影可用）。
        """
        if self._ball is not None:
            self._ball.setVisibility(View.VISIBLE)
            return
        sw, sh = self._screen()
        size = self._dp(56)
        pad = self._dp(7)
        total = size + pad * 2

        root = FrameLayout(activity)
        # 投影：比球略大、往下偏一点的黑圆
        shadow = View(activity)
        shadow.setBackground(_oval(_argb(28, 0, 0, 0)))
        slp = FrameLayoutLP(size, size, Gravity.TOP | Gravity.START)
        slp.leftMargin = pad
        slp.topMargin = pad + self._dp(2)
        root.addView(shadow, slp)

        ball = View(activity)
        ball.setBackground(_oval(C_BLUE, self._dp(0.5), _argb(60, 255, 255, 255)))
        blp = FrameLayoutLP(size, size, Gravity.TOP | Gravity.START)
        blp.leftMargin = pad
        blp.topMargin = pad
        root.addView(ball, blp)

        glyph = TextView(activity)
        glyph.setText(_s(self._icon_glyph('search', '搜')))
        glyph.setTypeface(self._icon_typeface())
        glyph.setTextColor(_argb(255, 255, 255, 255))
        glyph.setTextSize(22.0)
        glyph.setGravity(Gravity.CENTER)
        glp = FrameLayoutLP(size, size, Gravity.TOP | Gravity.START)
        glp.leftMargin = pad
        glp.topMargin = pad
        root.addView(glyph, glp)

        lp = WMLP(total, total, WMLP.TYPE_APPLICATION_OVERLAY,
                  WMLP.FLAG_NOT_FOCUSABLE | WMLP.FLAG_NOT_TOUCH_MODAL,
                  PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP | Gravity.START
        lp.x = sw - total - self._dp(8)
        lp.y = int(sh * 0.55)

        def on_down():
            self._ball_base = (lp.x, lp.y)
            ball.setAlpha(0.55)
            glyph.setAlpha(0.55)
            root.animate().scaleX(0.94).scaleY(0.94).setDuration(90).start()

        def on_up():
            ball.setAlpha(1.0)
            glyph.setAlpha(1.0)
            root.animate().scaleX(1.0).scaleY(1.0).setDuration(150).start()

        def on_drag(dx, dy):
            lp.x = int(self._ball_base[0] + dx)
            lp.y = int(self._ball_base[1] + dy)
            try:
                self._wm.updateViewLayout(root, lp)
            except Exception:
                pass

        # 统一按位置传参：(on_tap, on_drag, on_down, on_up, slop)
        tl = _OnTouchListener(self.ball_tap_default, on_drag, on_down, on_up,
                              self._dp(6))
        self._ball_proxies = [tl]
        root.setOnTouchListener(tl)
        try:
            self._wm.addView(root, lp)
        except Exception:
            # 之前这里是裸调用：失败会被 p4a 的 Runnable.run 里那个裸 except
            # 吞成一行 traceback，用户只看到"点球没反应"
            traceback.print_exc()
            from kivy.logger import Logger
            Logger.error('[fojiao] 悬浮球添加窗口失败（悬浮窗权限被收回？）')
            self._unbind(root, 'touch')
            self._ball_proxies = []
            return

        # 入场：淡入 + 轻微放大
        root.setAlpha(0.0)
        root.animate().alpha(1.0).setDuration(200).setInterpolator(
            DecelerateInterpolator()).start()
        self._ball = root
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
            # 顺序要紧：先解绑监听，再丢代理引用（理由见 _unbind）
            self._unbind(self._ball, 'touch')
            self._ball = None
            self._ball_lp = None
            self._ball_proxies = []

    def _ball_visible_ui(self, visible):
        """显隐悬浮球（线程无关：内部派发到 UI 线程）。"""
        self._ui_call(lambda: self._apply_ball_visible(visible))

    def _apply_ball_visible(self, visible):
        """(UI 线程) 真正的显隐。"""
        if self._ball is not None:
            self._ball.setVisibility(View.VISIBLE if visible else View.INVISIBLE)

    def _panel_visible_ui(self, visible):
        """显隐面板（截图时把面板也藏起来，避免被截进画面）。"""
        self._ui_call(lambda: self._apply_panel_visible(visible))

    def _apply_panel_visible(self, visible):
        """(UI 线程) 真正的显隐。"""
        if self._panel is not None:
            self._panel.setVisibility(View.VISIBLE if visible else View.INVISIBLE)

    @run_on_ui_thread
    def move_task_to_back(self):
        activity.moveTaskToBack(True)

    # ---------------- 悬浮答案面板 ----------------
    def show_panel(self, text=''):
        self._ui_call(lambda: self._show_panel_ui(text))

    def _show_panel_ui(self, text):
        """(UI 线程) 建/显示面板；text 非空则整段替换答案区。"""
        if self._panel is None:
            self._build_panel()
        if self._panel is not None:
            self._panel.setVisibility(View.VISIBLE)
        if text:
            if self._answer_tv is not None:
                # 同步 Python 侧持有的全文，否则后续追问会拼到旧文本上
                self._panel_text = self._clip_panel_text(text)
                self._answer_tv.setText(_s(self._panel_text))
            # 有结果了就收起"正在…"
            if self._status_row is not None:
                self._status_row.setVisibility(View.GONE)

    def _build_panel(self):
        """浅色 iOS 风面板：白色圆角卡片 + 蓝色主按钮 + 八段加载指示器。

        面板会挡住题目，所以标题栏上放了一个"拖动改大小"的手柄：
        拖它就能把面板收小/放大，尺寸在本次运行内记住（关掉再开还是这个大小）。
        """
        # 本轮新建的代理都收在这里，关面板时一起丢（见 _panel_proxies 的说明）
        self._panel_proxies = []
        sw, sh = self._screen()
        if self._panel_size:
            pw, ph = self._panel_size
        else:
            pw, ph = int(sw * 0.90), int(sh * 0.52)
        pw = max(self._dp(240), min(sw, pw))
        ph = max(self._dp(200), min(sh, ph))

        root = LinearLayout(activity)
        root.setOrientation(LinearLayout.VERTICAL)
        root.setBackground(_rounded(0xFFFFFFFF, self._dp(20)))
        root.setPadding(self._dp(16), self._dp(12), self._dp(16), self._dp(14))

        # 标题栏（拖动区）+ 改大小 + 关闭
        # （原来的「—」最小化按钮已删：每次点开悬浮球都是一道新题，
        #   关面板只留 × 一个出口，路径更短、也不会留下隐藏的面板窗口）
        title = LinearLayout(activity)
        title.setOrientation(LinearLayout.HORIZONTAL)
        title.setGravity(Gravity.CENTER_VERTICAL)
        tv = TextView(activity)
        tv.setText(_s('解题助手'))
        tv.setTextColor(_argb(255, 28, 28, 30))
        tv.setTextSize(15.0)
        tv.setTypeface(Typeface.DEFAULT_BOLD)
        title.addView(tv, LLLP(0, -2, 1.0))
        btn_resize = self._icon_btn('resize', '↘')
        btn_close = self._round_icon_btn('×')
        title.addView(btn_resize, self._lp(self._dp(30), self._dp(30), 0, 8))
        title.addView(btn_close, self._lp(self._dp(30), self._dp(30)))
        root.addView(title, LLLP(-1, -2))

        sep = View(activity)
        sep.setBackgroundColor(_c(C_SEP))
        root.addView(sep, LLLP(-1, max(1, self._dp(0.6))))

        # 状态行（加载指示器 + 文案），平时隐藏
        srow = LinearLayout(activity)
        srow.setOrientation(LinearLayout.HORIZONTAL)
        srow.setGravity(Gravity.CENTER_VERTICAL)
        srow.setPadding(0, self._dp(10), 0, 0)
        loader = self._make_loader(self._dp(16))
        # _loader_view 到 addView 成功后再赋值（失败时不留指向未挂上视图的引用）
        srow.addView(loader, LLLP(self._dp(16), self._dp(16)))
        stv = TextView(activity)
        stv.setTextColor(_argb(255, 142, 142, 147))
        stv.setTextSize(13.0)
        stv.setPadding(self._dp(8), 0, 0, 0)
        srow.addView(stv, LLLP(0, -2, 1.0))
        srow.setVisibility(View.GONE)
        root.addView(srow, LLLP(-1, -2))

        # 答案区
        sv = ScrollView(activity)
        sv.setFillViewport(True)
        ans = TextView(activity)
        ans.setTextColor(_argb(255, 58, 58, 60))
        ans.setTextSize(15.0)
        try:
            ans.setLineSpacing(0.0, 1.18)
        except Exception:
            pass
        ans.setText(_s(PANEL_INTRO))
        ans.setPadding(0, self._dp(10), 0, self._dp(10))
        sv.addView(ans)
        root.addView(sv, LLLP(-1, 0, 1.0))
        # _panel_text / _answer_sv 留到 addView 成功后再赋值：失败时不能留下
        # 指向那棵没挂上的视图树的引用

        # 输入行：药丸形输入框（点它直接弹键盘）+ 圆形「问」
        # 两者都 44dp 高、垂直居中，间距 10dp —— 尺寸不齐会很显眼
        row_h = self._dp(44)
        row = LinearLayout(activity)
        row.setOrientation(LinearLayout.HORIZONTAL)
        row.setGravity(Gravity.CENTER_VERTICAL)
        edit = EditText(activity)
        edit.setHint(_s('输入题目，或接着追问…'))
        edit.setTextColor(_argb(255, 28, 28, 30))
        edit.setHintTextColor(_argb(255, 142, 142, 147))
        edit.setTextSize(14.5)
        edit.setMaxLines(3)
        # 单行时文字要垂直居中（多行时整块居中）
        edit.setGravity(Gravity.CENTER_VERTICAL | Gravity.START)
        # 药丸形输入框（iOS 聊天框那种），圆角取高度的一半
        edit.setBackground(_rounded(C_GRAY_FILL, row_h / 2.0))
        edit.setPadding(self._dp(16), self._dp(4), self._dp(16), self._dp(4))
        btn_send = self._circle_btn('问', size_pt=14.0)
        row.addView(edit, LLLP(0, row_h, 1.0))
        lp_send = LLLP(row_h, row_h)
        lp_send.setMargins(self._dp(10), 0, 0, 0)
        row.addView(btn_send, lp_send)
        # 输入行和上面的答案区留一点呼吸间距，文字不要贴着输入框
        lp_row = LLLP(-1, -2)
        lp_row.setMargins(0, self._dp(8), 0, 0)
        root.addView(row, lp_row)

        # 功能按钮行：主操作实心蓝，其余淡蓝/浅灰
        row2 = LinearLayout(activity)
        row2.setOrientation(LinearLayout.HORIZONTAL)
        btn_shot = self._pill('截图搜题', 'filled', size=12.5, bold=True)
        btn_latest = self._pill('最新截图', 'tinted', size=12.5)
        btn_clip = self._pill('读剪贴板', 'tinted', size=12.5)
        btn_copy = self._pill('复制答案', 'gray', size=12.5)
        for i, b in enumerate((btn_shot, btn_latest, btn_clip, btn_copy)):
            row2.addView(b, self._lp(0, self._dp(44), 1.0,
                                     0 if i == 0 else 6))
        root.addView(row2, self._lp(-1, self._dp(48), 0, 0))

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

        # 同样按位置传参：(on_tap, on_drag, on_down, on_up, slop)
        tl = _OnTouchListener(None, on_drag, on_down, None, self._dp(8))
        self._panel_proxies.append(tl)
        title.setOnTouchListener(tl)

        # 标题栏上那个手柄：拖它改面板大小（左上角固定，往右下扩）
        min_w, min_h = self._dp(240), self._dp(200)

        def on_resize_down():
            self._resize_base = (lp.width, lp.height)

        def on_resize_drag(dx, dy):
            base_w, base_h = self._resize_base or (lp.width, lp.height)
            lp.width = int(max(min_w, min(base_w + dx, sw - lp.x)))
            lp.height = int(max(min_h, min(base_h + dy, sh - lp.y)))
            try:
                self._wm.updateViewLayout(root, lp)
            except Exception:
                pass
            self._panel_size = (lp.width, lp.height)

        rl = _OnTouchListener(None, on_resize_drag, on_resize_down, None,
                              self._dp(4))
        self._panel_proxies.append(rl)
        btn_resize.setOnTouchListener(rl)
        # 手柄只是用来拖的，点它别去触发"最小化"之类的行为
        btn_resize.setClickable(False)

        def _show_keyboard():
            """点输入框就弹软键盘。

            悬浮窗不是普通 Activity 的窗口，有的 ROM 不会自动弹，
            所以这里显式 requestFocus + showSoftInput 一次；同时让面板
            按 resize 模式避让，输入框不会被键盘盖住。
            """
            edit.requestFocus()
            try:
                lp.softInputMode = (WMLP.SOFT_INPUT_ADJUST_RESIZE
                                    | WMLP.SOFT_INPUT_STATE_VISIBLE)
                self._wm.updateViewLayout(root, lp)
            except Exception:
                pass
            try:
                imm = cast('android.view.inputmethod.InputMethodManager',
                           activity.getSystemService(
                               Context.INPUT_METHOD_SERVICE))
                imm.showSoftInput(edit, 0)
            except Exception:
                traceback.print_exc()

        def _close():
            """关面板并把它占的东西全部还回去。

            顺序不能改：
              1) removeView —— 先断开 Java 侧对视图的持有（窗口没了，
                 View 树才可能被回收）
              2) 解绑各控件的监听 —— 代理的闭包捕获了 root/edit/lp，
                 只要还有控件引用着代理，整棵树就活着；而 pyjnius 不保证
                 Java 侧持有期间 Python 对象不被回收，所以必须解绑在前
              3) _drop_panel_refs() —— 停掉无限加载动画、清空成员、
                 最后丢掉代理引用，整棵面板树这时才可回收
            """
            try:
                self._wm.removeView(root)
            except Exception:
                pass
            for v, k in ((title, 'touch'), (btn_resize, 'touch'),
                         (edit, 'click')):
                self._unbind(v, k)
            for b in (btn_close, btn_send, btn_clip, btn_shot, btn_latest,
                      btn_copy):
                self._unbind(b)
            self._drop_panel_refs()
            from kivy.logger import Logger
            Logger.info('[fojiao] 面板已关闭并释放（长期代理仍有 %d 个）'
                        % len(self._proxies))

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
            """「问」：有上下文就接着这道题追问，没有就当新问题。

            注意：pyjnius 有时已经把 getText() 的 Editable 转成 Python str，
            这时再调 .toString() 会 AttributeError（实测踩过），两种都要兼容
            """
            try:
                t = edit.getText()
                q = str(t if isinstance(t, str) else t.toString()).strip()
            except Exception:
                q = ''
            if not q:
                self._set_answer('请先在输入框里输入要问的内容。')
                return
            self._ui_call(lambda: edit.setText(_s('')))
            if ai_core.has_conversation():
                self.followup_async(q)
            else:
                self.solve_text_async(q)

        def _copy():
            if self._last_answer:
                self.set_clipboard(self._last_answer)
                self.toast('答案已复制到剪贴板')
            else:
                self.toast('还没有可复制的答案')

        for btn, cb in ((btn_close, _close),
                        (btn_send, _send),
                        (btn_clip, _read_clip),
                        (btn_shot, self.capture_and_solve),
                        (btn_latest, self.solve_from_latest_shot),
                        (btn_copy, _copy)):
            p = _OnClickListener(cb)
            self._panel_proxies.append(p)
            btn.setOnClickListener(p)

        # 点输入框直接弹键盘（原来是旁边一个「键盘」按钮，去掉了）
        kbd = _OnClickListener(_show_keyboard)
        self._panel_proxies.append(kbd)
        edit.setOnClickListener(kbd)

        try:
            self._wm.addView(root, lp)
        except Exception:
            # 之前是裸调用：失败会被 p4a 的 Runnable.run 里那个裸 except
            # 吞成一行 traceback，用户只看到"点球没反应"
            traceback.print_exc()
            from kivy.logger import Logger
            Logger.error('[fojiao] 面板添加窗口失败（悬浮窗权限被收回？）')
            for v, k in ((title, 'touch'), (btn_resize, 'touch'), (edit, 'click'),
                         (btn_close, 'click'), (btn_send, 'click'),
                         (btn_clip, 'click'), (btn_shot, 'click'),
                         (btn_latest, 'click'), (btn_copy, 'click')):
                self._unbind(v, k)
            self._drop_panel_refs()
            return
        self._panel = root
        self._panel_lp = lp
        self._answer_tv = ans
        self._edit = edit
        self._status_row = srow
        self._status_tv = stv
        self._panel_text = PANEL_INTRO
        self._answer_sv = sv
        self._loader_view = loader

        # 入场：淡入 + 轻微上浮
        root.setAlpha(0.0)
        root.setTranslationY(float(self._dp(10)))
        root.animate().alpha(1.0).translationY(0.0).setDuration(
            220).setInterpolator(DecelerateInterpolator()).start()
        from kivy.logger import Logger
        Logger.info('[fojiao] 面板已建立（本轮 %d 个代理，视图约 %d 个）'
                    % (len(self._panel_proxies), root.getChildCount()))

    # ---------------- 组件工厂 ----------------
    def _lp(self, w, h, weight=0.0, margin=0):
        """LinearLayout 参数：w/h 传 -1/-2 表示 match_parent/wrap_content。"""
        lp = LLLP(w, h, weight)
        m = self._dp(margin)
        lp.setMargins(m, self._dp(2), 0, self._dp(2))
        return lp

    def _round_icon_btn(self, glyph):
        """标题栏右上角那种小圆按钮（浅灰底 + 灰色字）。"""
        b = TextView(activity)
        b.setText(_s(glyph))
        b.setTextSize(15.0)
        b.setTextColor(_argb(255, 142, 142, 147))
        b.setGravity(Gravity.CENTER)
        b.setBackground(_stateful(_oval(C_GRAY_FILL), _oval(C_GRAY_FILL2)))
        return b

    def _icon_btn(self, name, fallback, size_pt=15.0):
        """带图标的小圆按钮（图标字体缺失时退回 fallback 里的文字符号）。"""
        glyph = self._icon_glyph(name, fallback)
        b = self._round_icon_btn(glyph)
        b.setTextSize(size_pt)
        if glyph == _ICON_GLYPH.get(name):
            b.setTypeface(self._icon_typeface())
        return b

    def _pill(self, text, kind='tinted', size=13.0, bold=False):
        """iOS 风按钮：filled(实心蓝) / tinted(淡蓝底) / gray(浅灰底)。"""
        text_color = {'filled': (255, 255, 255), 'tinted': (10, 132, 255),
                      'gray': (28, 28, 30)}.get(kind, (10, 132, 255))
        fill = {'filled': C_BLUE, 'tinted': C_BLUE_TINT,
                'gray': C_GRAY_FILL}.get(kind, C_BLUE_TINT)
        press = {'filled': C_BLUE_DARK, 'tinted': 0xFFD8E9FF,
                 'gray': C_GRAY_FILL2}.get(kind, 0xFFD8E9FF)
        b = Button(activity)
        b.setText(_s(text))
        b.setTextSize(size)
        b.setTextColor(_argb(255, *text_color))
        r = self._dp(11)
        b.setBackground(_stateful(_rounded(fill, r), _rounded(press, r)))
        try:
            b.setAllCaps(False)
            b.setTypeface(Typeface.DEFAULT_BOLD if bold else Typeface.DEFAULT)
            # Material 主题给 Button 设了最小尺寸，会撑坏行高，这里清零
            b.setMinWidth(0)
            b.setMinimumWidth(0)
            b.setMinHeight(0)
            b.setMinimumHeight(0)
        except Exception:
            pass
        b.setPadding(self._dp(4), 0, self._dp(4), 0)
        return b

    def _circle_btn(self, text, size_pt=17.0, kind='filled', bold=True):
        """圆形按钮（输入框右边那个「问」）。

        直径由调用方用 LayoutParams 给（宽高相等就是正圆）——
        这里只管颜色、字号、清零系统默认的最小尺寸和 padding。
        """
        b = Button(activity)
        b.setText(_s(text))
        b.setTextSize(size_pt)
        text_color = {'filled': (255, 255, 255), 'tinted': (10, 132, 255),
                      'gray': (28, 28, 30)}.get(kind, (255, 255, 255))
        fill = {'filled': C_BLUE, 'tinted': C_BLUE_TINT,
                'gray': C_GRAY_FILL}.get(kind, C_BLUE)
        press = {'filled': C_BLUE_DARK, 'tinted': 0xFFD8E9FF,
                 'gray': C_GRAY_FILL2}.get(kind, C_BLUE_DARK)
        b.setTextColor(_argb(255, *text_color))
        b.setBackground(_stateful(_oval(fill), _oval(press)))
        try:
            b.setAllCaps(False)
            b.setTypeface(Typeface.DEFAULT_BOLD if bold else Typeface.DEFAULT)
            b.setGravity(Gravity.CENTER)
            b.setMinWidth(0)
            b.setMinimumWidth(0)
            b.setMinHeight(0)
            b.setMinimumHeight(0)
        except Exception:
            pass
        b.setPadding(0, 0, 0, 0)
        return b

    def _make_loader(self, size):
        """iOS 八段加载指示器：8 根小段沿圆周排开、整体匀速旋转。

        旋转用 RotateAnimation（定参构造函数，jnius 调得动）；
        一开始用的是 ObjectAnimator.ofFloat(view, 'rotation', [0, 360])，
        在真机上会抛异常（jnius 处理不了那个变参签名，实测踩过），
        所以改成 RotateAnimation。任何一步失败都退回系统 ProgressBar。
        """
        size = int(size)
        frame = FrameLayout(activity)
        # 上一轮的动画别留着：如果上一次建面板在 addView 之前就抛了异常，
        # 这里只是丢掉引用是不会停掉那个 setRepeatCount(-1) 的动画的
        if self._loader_anim is not None:
            try:
                self._loader_anim.cancel()
            except Exception:
                pass
            self._loader_anim = None
        try:
            n = 8
            bar_w = max(2, int(size * 0.16))
            bar_h = max(4, int(size * 0.30))
            for i in range(n):
                v = View(activity)
                v.setBackground(_rounded(C_BLUE, bar_w / 2.0))
                lp = FrameLayoutLP(bar_w, bar_h,
                                   Gravity.TOP | Gravity.CENTER_HORIZONTAL)
                frame.addView(v, lp)
                # 绕容器中心转到第 i 个位置，透明度依次变淡（追光效果）
                v.setPivotX(bar_w / 2.0)
                v.setPivotY(size / 2.0)
                v.setRotation(i * 45.0)
                v.setAlpha(1.0 - i * 0.10)
            anim = RotateAnimation(
                0.0, 360.0,
                Anim.RELATIVE_TO_SELF, 0.5,
                Anim.RELATIVE_TO_SELF, 0.5)
            anim.setDuration(1000)
            anim.setRepeatCount(-1)          # Animation.INFINITE
            anim.setInterpolator(LinearInterpolator())
            frame.startAnimation(anim)
            self._loader_anim = anim
            return frame
        except Exception:
            traceback.print_exc()
            try:
                pb = ProgressBar(activity)
                pb.getIndeterminateDrawable().setTint(_c(C_BLUE))
                return pb
            except Exception:
                return frame

    def _icon_typeface(self):
        """图标字体（lucide.ttf 随包放在 files/app）；拿不到就返回默认字体。"""
        if self._typeface is not None:
            return self._typeface
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'lucide.ttf')
            if os.path.exists(path):
                self._typeface = Typeface.createFromFile(path)
                self._icon_font_ok = True
                return self._typeface
        except Exception:
            traceback.print_exc()
        self._typeface = Typeface.DEFAULT
        self._icon_font_ok = False
        return self._typeface

    def _icon_glyph(self, name, fallback):
        """有图标字体就用图标码位，否则退回汉字。"""
        # 必须先解析一次字体：_icon_font_ok 是在 _icon_typeface() 里点亮的，
        # 而调用方都是"先取 glyph、再 setTypeface"。不在这里先点亮的话，
        # 进程内第一次会返回 fallback 汉字，却仍被套上 lucide 字体
        # （汉字在 lucide 里没有字形 → 渲染成豆腐块）
        self._icon_typeface()
        code = _ICON_GLYPH.get(name)
        if code and self._icon_font_ok:
            return code
        return fallback

    # ---------------- 状态行 ----------------
    def _set_status(self, text):
        """在面板状态行上显示"正在…"（text 为空则隐藏状态行）。"""
        if self._panel is None:
            self.show_panel('')
        self._ui_call(lambda: self._set_status_ui(text))

    def _set_status_ui(self, text):
        """(UI 线程，由 _ui_call 派发) 刷新"正在…"状态行。"""
        if self._status_row is None:
            return
        if text:
            self._status_tv.setText(_s(text))
            self._status_row.setVisibility(View.VISIBLE)
        else:
            self._status_row.setVisibility(View.GONE)

    def _set_answer(self, text):
        if self._panel is None:
            self.show_panel(text)
            return
        self._ui_call(lambda: self._set_answer_ui(text))

    def _error_text(self, err):
        """把异常转成给用户看的一句话。

        全黑那种情况本身就是"说明+替代方案"，前面再挂"错误："会读起来很怪。
        """
        msg = str(err)
        if msg.startswith('画面全黑'):
            return msg
        return '错误：' + msg

    def _set_answer_ui(self, text):
        """(UI 线程，由 _ui_call 派发) 整段替换答案区。"""
        self._panel_text = self._clip_panel_text(text)
        if self._answer_tv is not None:
            self._answer_tv.setText(_s(self._panel_text))
        # 出结果了就把"正在…"状态行收起来
        if self._status_row is not None:
            self._status_row.setVisibility(View.GONE)

    def _ui_append(self, text):
        """把一段文字追加到答案区（追问的问答往后面接）。"""
        self._ui_call(lambda: self._append_ui(text))

    #: 面板全文上限。追问越多、拼出来的问答越长，而每次刷新都是整段
    #: setText 重排（ScrollView 还开着 FillViewport），长到几万字会明显卡。
    _PANEL_TEXT_LIMIT = 20000

    @classmethod
    def _clip_panel_text(cls, text):
        """超长只留尾部 —— 最新的问答才是有用的。"""
        if len(text) <= cls._PANEL_TEXT_LIMIT:
            return text
        return ('（前面的内容太长，已省略）\n\n'
                + text[-cls._PANEL_TEXT_LIMIT:])

    def _reset_answer_area(self):
        """新题目开始时复位答案区：每次点开悬浮球都是一道新题。"""
        self._ui_call(self._reset_answer_ui)

    def _reset_answer_ui(self):
        """(UI 线程，由 _ui_call 派发) 复位成引导语。"""
        if self._answer_tv is None:
            return
        self._panel_text = PANEL_INTRO
        self._answer_tv.setText(_s(PANEL_INTRO))
        if self._status_row is not None:
            self._status_row.setVisibility(View.GONE)

    def _append_ui(self, text):
        """(UI 线程，由 _ui_call 派发) 往答案区追加一段。"""
        if self._answer_tv is None:
            return
        cur = (self._panel_text or '').strip()
        if not cur or cur == PANEL_INTRO.strip():
            self._panel_text = text
        else:
            self._panel_text = self._panel_text.rstrip() + '\n\n' + text
        self._panel_text = self._clip_panel_text(self._panel_text)
        self._answer_tv.setText(_s(self._panel_text))
        if self._status_row is not None:
            self._status_row.setVisibility(View.GONE)
        # 滚到底部，最新一条才看得见
        try:
            if self._answer_sv is not None:
                self._answer_sv.fullScroll(View.FOCUS_DOWN)
        except Exception:
            pass

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
        made = [bmp]
        try:
            w, h = bmp.getWidth(), bmp.getHeight()
            if w > max_w:
                # 按原始分辨率解出来的那张很大（1440x3200 约 18MB），
                # 缩放完就用不上了，和缩放图一起在 finally 里回收
                small = Bitmap.createScaledBitmap(bmp, max_w,
                                                  int(h * max_w / float(w)),
                                                  True)
                made.append(small)
                bmp = small
            out = FileOutputStream(dst_path)
            try:
                bmp.compress(BitmapFormat.JPEG, 88, out)
            finally:
                out.close()
        finally:
            for b in made:
                _recycle(b)
        return dst_path

    def solve_from_latest_shot(self):
        """读相册里最新的一张截图去搜题。"""
        if not self._claim_busy():
            self.toast('正在处理上一题，请稍候…')
            return
        try:
            self._set_status('正在查找最新截图…')
            threading.Thread(target=self._latest_shot_worker,
                             daemon=True).start()
        except Exception:
            traceback.print_exc()      # 起不了线程就别把 _busy 留着
            self._release_busy()

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
            self._set_status('正在识别题目…')
            q = ai_core.ocr_question(dst)
            self._set_status('正在解答…')
            answer = ai_core.solve_question(q)
            self._last_answer = answer
            # 同截图搜题：题目不用重复贴，只回答案
            self._set_answer('【解答】\n' + ai_core.plain_text(answer))
        except Exception as e:
            traceback.print_exc()
            self._set_answer(self._error_text(e))
        finally:
            self._release_busy()

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
        if not self._claim_busy():
            self.toast('正在处理上一题，请稍候…')
            return
        try:
            self.show_panel('【题目】\n' + ai_core.font_safe(question[:400]))
            self._set_status('正在解答…')
        except Exception:
            traceback.print_exc()      # 别把 _busy 永久留下
            self._release_busy()
            return

        def worker():
            try:
                answer = ai_core.solve_question(question)
                self._last_answer = answer
                self._set_answer('【题目】\n' + ai_core.font_safe(question) +
                                 '\n\n【解答】\n' +
                                 ai_core.plain_text(answer))
            except Exception as e:
                traceback.print_exc()
                self._set_answer(self._error_text(e))
            finally:
                self._release_busy()
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            traceback.print_exc()      # 起不了线程就别把 _busy 留着
            self._release_busy()

    def followup_async(self, question):
        """追问：接着当前这道题继续问，问答往面板后面接。

        和 solve_text_async 的区别只在 ai_core 那侧 —— 追问问的是
        followup_question()，会把前面的题目和解答一起带上，
        所以"为什么选 B"这种问题模型看得懂在问哪道题。
        """
        if not self._claim_busy():
            self.toast('正在处理上一题，请稍候…')
            return
        try:
            self._ui_append('【追问】' + ai_core.font_safe(question))
            self._set_status('正在追问…')
        except Exception:
            traceback.print_exc()      # 别把 _busy 永久留下
            self._release_busy()
            return

        def worker():
            try:
                answer = ai_core.followup_question(question)
                self._last_answer = answer
                self._ui_append('【解答】\n' + ai_core.plain_text(answer))
            except Exception as e:
                traceback.print_exc()
                self._ui_append(self._error_text(e))
            finally:
                self._release_busy()
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            traceback.print_exc()      # 起不了线程就别把 _busy 留着
            self._release_busy()

    def capture_and_solve(self):
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
        if not self._claim_busy():
            self.toast('正在处理上一题，请稍候…')
            return
        try:
            self.show_panel('')
            self._reset_answer_area()      # 新题目：把上一题的答案清掉
            self._set_status('正在截图…')
            self._ball_visible_ui(False)
            self._panel_visible_ui(False)
            # jnius 对象的 str() 是对象表示，不是路径，必须取绝对路径
            path = os.path.join(activity.getCacheDir().getAbsolutePath(),
                                'fojiao_shot.jpg')
        except Exception:
            # 准备阶段出错必须把 _busy 还回去：否则用户会永久看到
            # "正在处理上一题"，而且球和面板已经被藏起来了
            traceback.print_exc()
            self._release_busy()
            self._ball_visible_ui(True)
            self._panel_visible_ui(True)
            return

        def worker():
            try:
                self._do_capture(path)
                self._set_status('正在识别题目…')
                q = ai_core.ocr_question(path)
                self._set_status('正在解答…')
                answer = ai_core.solve_question(q)
                self._last_answer = answer
                # 题目就在屏幕上看着，面板又小，只回答案不重复贴题目
                self._set_answer('【解答】\n' + ai_core.plain_text(answer))
            except Exception as e:
                traceback.print_exc()
                self._set_answer(self._error_text(e))
            finally:
                self._release_busy()
                self._ball_visible_ui(True)
                self._panel_visible_ui(True)
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            # 起不了线程时必须还回 _busy 并把刚藏起来的球/面板恢复，
            # 否则用户界面全空、只能重开 App
            traceback.print_exc()
            self._release_busy()
            self._ball_visible_ui(True)
            self._panel_visible_ui(True)

    # ---------------- MediaProjection 截屏 ----------------
    def _release_capture_session(self):
        """(主线程) 关掉常驻的 reader / virtual display。

        正在读帧时不能关：read() 线程可能正卡在 acquireLatestImage() /
        getPlanes() 上，关掉它轻则抛 IllegalStateException，重则 native
        崩溃（Python 侧连 traceback 都看不到，正是"偶现闪退"的形态）。
        所以这时只打"失效"标记，等 read() 自己的 finally 收尾再真正释放。
        """
        with self._lock:
            if self._capturing:
                self._session_dead = True
                return
        self._close_capture_session()

    def _close_capture_session(self):
        """(主线程) 真正 close 掉 reader / virtual display。"""
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
        self._had_session = True
        self._session_dead = False
        # 建新会话前必须把旧的真的关掉（硬释放：此刻不可能在读帧）
        self._close_capture_session()
        sw, sh = self._screen()
        # 先建成局部变量，两步都成功了才写进 self：createVirtualDisplay 抛错时
        # 不会留下"reader 已赋值、_reader_size 还是旧值"的半成品状态
        # （那会让后续读帧走进 row//4 x 0 的非法分支，报出与真实原因无关的错）
        reader = ImageReader.newInstance(sw, sh, PixelFormat.RGBA_8888, 2)
        vd = self._projection.createVirtualDisplay(
            'fojiao-cap', sw, sh, self._dpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.getSurface(), None, None)
        self._reader = reader
        self._reader_size = (sw, sh)
        self._vd = vd
        from kivy.logger import Logger
        Logger.info('[fojiao] 截屏会话已建立 %dx%d（此后一直复用）' % (sw, sh))

    @run_on_ui_thread
    def start_capture_session(self):
        try:
            # 没有注册成功的回调时不要建会话：系统不送帧，而一个投影只能
            # 成功建一次 VirtualDisplay，白建一次这一轮授权就废了。
            if self._proj_cb is None:
                from kivy.logger import Logger
                Logger.error('[fojiao] 回调未注册，跳过建立截屏会话')
                self._set_answer(
                    '截屏回调注册失败，本次无法截屏。\n\n'
                    '请重新点一次「② 开启截屏授权」；'
                    '若一直失败，可先用面板上的【最新截图】。')
                return
            self._create_capture_session()
        except Exception:
            traceback.print_exc()

    def _do_capture(self, path):
        result = {}
        done = threading.Event()

        def start():
            """(UI 线程，由 _ui_call 派发) 起一个读帧线程并等它收尾。

            这里刻意不用 @run_on_ui_thread：那个装饰器是拿**函数对象**当 key
            永久缓存 Runnable 的（见 _p4a_src/.../android/runnable.py:14,50-53），
            而 start 是每次截图新建的闭包 —— 等于每截一次图就往
            __functionstable__ 里塞一项，永不清理，还各带一个 JNI global ref。
            走 _ui_call 语义一样（异步 post），但不留垃圾。
            """
            try:
                # 会话已被判定失效（投影被系统回收、释放被推迟到读帧结束）
                if self._session_dead:
                    result['err'] = ('截屏会话已失效（截屏授权被系统回收），'
                                     '请重新点「② 开启截屏授权」')
                    done.set()
                    return
                # 会话只在授权时建一次（见 _create_capture_session）；
                # 这里只兜底，且绝不重试建第二个。
                if self._reader is None and not self._session_attempted:
                    if self._proj_cb is None:
                        result['err'] = ('截屏回调没注册成功，系统不会送画面；'
                                         '请重新点「② 开启截屏授权」')
                        done.set()
                        return
                    self._create_capture_session()
                reader = self._reader
                sw, sh = self._reader_size
                if reader is None:
                    result['err'] = '截屏会话没建起来，请重新点「② 开启截屏授权」'
                    done.set()
                    return
                # "正在读帧"必须在**起线程之前**、在这条 UI 线程上置位：
                # 否则新线程还没拿到 GIL 时投影就被回收，_release_capture_session
                # 会看到 _capturing=False 而直接 close(reader)，读线程随后操作
                # 已关闭的 ImageReader —— 正是那种没有 traceback 的 native 崩溃。
                with self._lock:
                    if self._capturing:
                        result['err'] = '上一次截图还没收尾，请稍候再试'
                        done.set()
                        return
                    self._cap_gen += 1
                    gen = self._cap_gen
                    self._capturing = True

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
                        # Image 必须在 finally 里关：reader 只开了 2 个缓冲，
                        # 而会话按设计"绝不重建"，漏掉一个就等于永久少一个
                        # 缓冲，漏两次之后 acquireLatestImage() 会永远返回
                        # null（只能重新授权）。而拷贝/转码恰好是最容易抛
                        # 异常的一段。
                        # 本轮建的每张 Bitmap 都登记下来，回收统一放在
                        # finally 里 —— 必须在 _is_black() 和 compress()
                        # 之后（_is_black 是 except 吞异常的，图被提前
                        # 回收的话黑屏检测会静默失效）
                        made = []
                        try:
                            plane = img.getPlanes()[0]
                            row = plane.getRowStride()
                            pix = plane.getPixelStride()
                            buf = plane.getBuffer()
                            if row == sw * 4 and pix == 4:
                                bmp = Bitmap.createBitmap(sw, sh,
                                                          BitmapConfig.ARGB_8888)
                                made.append(bmp)
                                bmp.copyPixelsFromBuffer(buf)
                            else:  # 行对齐填充：先按 stride 建图再裁剪
                                full = Bitmap.createBitmap(row // 4, sh,
                                                           BitmapConfig.ARGB_8888)
                                made.append(full)
                                full.copyPixelsFromBuffer(buf)
                                # 源图是可变图且尺寸不同，这里必定是新对象
                                # （AOSP createBitmap 的"原样返回"只对不可变
                                # 且整幅裁剪生效），所以两张都能回收
                                bmp = Bitmap.createBitmap(full, 0, 0, sw, sh)
                                made.append(bmp)
                            # 整屏全黑 = 该应用禁止被截屏（系统隐私保护），
                            # 这种情况再往下发去 OCR 也是白花 token
                            if self._is_black(bmp):
                                result['err'] = BLACK_FRAME_HINT
                                result['black'] = True
                                return
                            if bmp.getWidth() > 1280:
                                bmp = Bitmap.createScaledBitmap(
                                    bmp, 1280,
                                    int(bmp.getHeight() * 1280 /
                                        bmp.getWidth()), True)
                                made.append(bmp)
                            out = FileOutputStream(path)
                            try:
                                bmp.compress(BitmapFormat.JPEG, 88, out)
                            finally:
                                out.close()
                            result['ok'] = True
                        finally:
                            # 全屏 ARGB_8888 一张约 10MB，不回收的话连续搜题
                            # 十几轮就把 Java 堆顶到 OOM（先卡后崩）
                            for b in made:
                                _recycle(b)
                            try:
                                img.close()
                            except Exception:
                                pass
                    except Exception as e:
                        result['err'] = str(e)
                    finally:
                        # 这里不能关 reader/vd —— 会话要留给下一次截图用。
                        # 但如果读帧期间投影被回收（那时 _release 只打了失效
                        # 标记、没敢关），现在必须由我们收尾，否则 reader/vd
                        # 就一直漏着。
                        with self._lock:
                            # 只有自己那一代才能清标志：15s 超时后旧线程可能
                            # 还在跑，不能把新线程的"正在读帧"清掉
                            owned = (self._cap_gen == gen)
                            if owned:
                                self._capturing = False
                            dead = self._session_dead
                            self._session_dead = False
                        if owned and dead:
                            # 必须再走一次 _release_capture_session（而不是直接
                            # _close_capture_session）：从上面解锁到这里，UI 线程
                            # 可能已经处理了一条新的截图请求并把 _capturing 又
                            # 置成了 True —— 直接 close 就会在别人读帧时把
                            # reader 关掉（native 崩溃）。交给守卫重新判断：
                            # 真的没人读才关，否则再打一次失效标记给新线程收尾。
                            self._ui_call(self._release_capture_session)
                        done.set()
                try:
                    threading.Thread(target=read, daemon=True).start()
                except Exception as e:
                    # 起不了线程（线上典型是线程/内存耗尽）：_capturing 是我们
                    # 在 UI 线程置的位，这里必须自己复位，否则后续所有截图都会
                    # 卡在"上一次截图还没收尾"，会话也永远释放不掉
                    with self._lock:
                        if self._cap_gen == gen:
                            self._capturing = False
                    result['err'] = '起读帧线程失败：%s' % e
                    done.set()
            except Exception as e:
                result['err'] = str(e)
                done.set()

        if not self._ui_call(start):
            # 派发失败没人会 set done，直接给出真实原因，别干等 15 秒
            raise RuntimeError('截图失败：无法把取帧任务投递到主线程')
        done.wait(15)
        if result.get('ok'):
            return True
        if result.get('black'):
            # 全黑是"该应用禁止截屏"，提示本身就是给用户看的说明，
            # 不用再套一层"截图失败："前缀
            raise RuntimeError(result['err'])
        # 千万别在这里关掉会话：Android 14+ 上一个 MediaProjection 只能成功
        # 建一次 VirtualDisplay，关掉就再也建不回来（实测反复重建的结果是
        # 一帧都收不到）。会话留给下一次截图继续用。
        raise RuntimeError('截图失败：' + result.get('err', '超时'))

    def _is_black(self, bmp):
        """整幅画面几乎全黑 → 基本可以断定该应用禁止被截屏。

        抽点采样（几十次 getPixel），比遍历全图快得多，也不占内存。
        """
        try:
            w, h = bmp.getWidth(), bmp.getHeight()
            for i in range(1, 9):
                for j in range(1, 15):
                    px = bmp.getPixel(int(w * i / 9.0), int(h * j / 15.0))
                    r = (px >> 16) & 0xFF
                    g = (px >> 8) & 0xFF
                    b = px & 0xFF
                    if r + g + b > 40:      # 有一点亮色就算有内容
                        return False
            return True
        except Exception:
            traceback.print_exc()
            return False


bridge = AndroidBridge() if ANDROID else None

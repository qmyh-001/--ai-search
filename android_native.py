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


    class _PJCallback(PythonJavaClass):
        """MediaProjection 回调：Android 14 起要求先注册回调才能创建虚拟屏；
        系统回收授权（锁屏、切换用户等）时也会回调 onStop。"""
        __javainterfaces__ = ['android/media/projection/MediaProjection$Callback']
        __javacontext__ = 'app'

        def __init__(self, on_stop):
            super().__init__()
            self._on_stop = on_stop

        @java_method('()V')
        def onStop(self):
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
        self._mpm = None
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
        _android_activity.bind(on_activity_result=self._on_activity_result)
        activity.startActivityForResult(self._mpm.createScreenCaptureIntent(),
                                        self.REQUEST_CODE)

    def _on_activity_result(self, requestCode, resultCode, data):
        if requestCode != self.REQUEST_CODE:
            return
        if resultCode == -1 and data is not None:
            try:
                self._projection = self._mpm.getMediaProjection(resultCode, data)
                try:
                    cb = _PJCallback(self._on_projection_stopped)
                    self._proxies.append(cb)
                    self._projection.registerCallback(cb, None)
                except Exception:
                    traceback.print_exc()
                self.toast('截屏授权成功，可以切到刷题App使用了')
            except Exception as e:
                self.toast('截屏授权失败：%s' % e)
        else:
            self.toast('未授予截屏权限')

    def _on_projection_stopped(self):
        self._projection = None
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

        tl = _OnTouchListener(on_tap=self.ball_tap_default,
                              on_drag=on_drag, on_down=on_down, on_up=on_up,
                              slop=self._dp(6))
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
        btn_copy = self._small_btn('复制答案')
        row2.addView(btn_clip, LLLP(0, -2, 1.0))
        row2.addView(btn_shot, LLLP(0, -2, 1.0))
        row2.addView(btn_copy, LLLP(0, -2, 1.0))
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

        tl = _OnTouchListener(on_drag=on_drag, on_down=on_down,
                              slop=self._dp(8))
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
                    self.toast('剪贴板为空或无权限读取，请长按输入框手动粘贴')
                    return
                self._ui_call(lambda: edit.setText(_s(text)))
                self.solve_text_async(text)
            threading.Thread(target=worker, daemon=True).start()

        def _send():
            q = str(edit.getText().toString()).strip()
            if not q:
                self.toast('请先输入题目')
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

    # ---------------- 搜题主流程 ----------------
    def ball_tap_default(self):
        action = self._cfg.get('tap_action', 'screenshot')
        if action == 'clipboard':
            text = self.get_clipboard().strip()
            if not text:
                self.show_panel(
                    '注意：没有读到剪贴板文字。\n\n'
                    'Android 10+ 只允许当前获焦的应用读剪贴板。\n'
                    '方法1：在题目界面长按选中题目文字 → 复制，'
                    '再点面板里的【读剪贴板】；\n'
                    '方法2：回到 App，在设置里把点按动作改为「截图搜题」。')
                return
            self.solve_text_async(text)
        else:
            self.capture_and_solve()

    def solve_text_async(self, question):
        if self._busy:
            self.toast('正在处理上一题，请稍候…')
            return
        self._busy = True
        self.show_panel('正在解答…\n\n【题目】\n' + question[:400])

        def worker():
            try:
                answer = ai_core.solve_question(question)
                self._last_answer = answer
                self._set_answer('【题目】\n' + question +
                                 '\n\n【解答】\n' + ai_core.plain_text(answer))
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
            self.toast('请先打开「佛脚AI搜题」完成一次截屏授权')
            return
        self._busy = True
        self.show_panel('正在截图…')
        self._ball_visible_ui(False)
        self._panel_visible_ui(False)
        path = os.path.join(str(activity.getCacheDir()), 'fojiao_shot.jpg')

        def worker():
            try:
                self._do_capture(path)
                self._set_answer('正在识别题目…')
                q = ai_core.ocr_question(path)
                self._set_answer('识别到题目：\n' + q + '\n\n正在解答…')
                answer = ai_core.solve_question(q)
                self._last_answer = answer
                self._set_answer('【题目】\n' + q +
                                 '\n\n【解答】\n' + ai_core.plain_text(answer))
            except Exception as e:
                traceback.print_exc()
                self._set_answer('错误：' + str(e))
            finally:
                self._busy = False
                self._ball_visible_ui(True)
                self._panel_visible_ui(True)
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- MediaProjection 截屏 ----------------
    def _do_capture(self, path):
        result = {}
        done = threading.Event()

        @run_on_ui_thread
        def start():
            try:
                sw, sh = self._screen()
                reader = ImageReader.newInstance(sw, sh, PixelFormat.RGBA_8888, 2)
                vd = self._projection.createVirtualDisplay(
                    'fojiao-cap', sw, sh, self._dpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    reader.getSurface(), None, None)

                def read():
                    try:
                        time.sleep(0.35)
                        img = reader.acquireLatestImage()
                        if img is None:
                            time.sleep(0.25)
                            img = reader.acquireLatestImage()
                        if img is None:
                            raise RuntimeError('未捕获到屏幕画面')
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
                        for x in (vd, reader):
                            try:
                                x.close()
                            except Exception:
                                try:
                                    x.release()
                                except Exception:
                                    pass
                        done.set()
                threading.Thread(target=read, daemon=True).start()
            except Exception as e:
                result['err'] = str(e)
                done.set()

        start()
        done.wait(15)
        if result.get('ok'):
            return True
        # 授权可能已被系统回收，作废掉，让状态页如实提示需要重新授权
        self._projection = None
        raise RuntimeError('截图失败：' + result.get('err', '超时'))


bridge = AndroidBridge() if ANDROID else None

# -*- coding: utf-8 -*-
"""佛脚AI搜题 —— 主程序（Kivy UI，iOS 亮色风）。

Android 端流程：
  1. 设置里填 DeepSeek API Key
  2. 申请悬浮窗权限
  3. 开启截屏授权（每次冷启动后需一次）
  4. 启动悬浮球 → 切到佛脚刷题 → 点悬浮球搜题
桌面端提供简单的调试界面（直接输入题目或本地图片路径测试 API）。

界面说明（这一版做了整体改版）：
  · 视觉：iOS 亮色风 —— 浅灰底 + 白色圆角卡片 + 柔和阴影 + SF 蓝主色
  · 图标：Lucide 图标字体（lucide.ttf，来自 github.com/lucide-icons/lucide，
    ISC 许可），观感接近 SF Symbols；字体缺失时图标自动隐藏，不影响使用
  · 动效：页面滑动切换、进入淡入、按钮按下压暗、iOS 八段加载指示器
  · 阴影不用 kivy 的 BoxShadow：它是 FBO 实现，在手机的 GLES2/虚拟 GPU 上
    又慢又可能出问题，这里用几层半透明圆角矩形叠出柔和边缘
  · 图标字体和中文不能混在同一个 Label 里（Lucide 没有汉字），所以图标一律
    单独用 IconLabel，文字用中文字体
"""
import os
import threading
import traceback

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.metrics import dp, sp
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty,
                             OptionProperty, StringProperty)
from kivy.storage.jsonstore import JsonStore
from kivy.core.window import Window
from kivy.graphics import (Color, Ellipse, Line, PopMatrix, PushMatrix, Rotate,
                           RoundedRectangle)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import Screen, ScreenManager  # noqa: F401
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

import ai_core

try:
    import android_native
    bridge = android_native.bridge
except Exception:
    android_native = None
    bridge = None

IS_ANDROID = platform == 'android'

FONT_FILE = 'NotoSansSC-Regular.otf'
#: Lucide 图标字体（见文件头说明）
ICON_FILE = 'lucide.ttf'

#: Lucide 图标码位（从 lucide-static 的 font/lucide.css 提取，
#: 形如 .icon-search::before{content:"\e151"}）
ICON = {
    'search': '\ue151',
    'settings': '\ue154',
    'book': '\ue05f',
    'info': '\ue0f9',
    'play': '\ue13c',
    'square': '\ue167',
    'shield': '\ue1ff',
    'screenshare': '\ue14f',
    'phone': '\ue163',
    'key': '\ue4a3',
    'cpu': '\ue0a9',
    'sparkles': '\ue412',
    'chevron': '\ue06f',
    'chevron_left': '\ue06e',
    'chevron_down': '\ue06d',
    'x': '\ue1b2',
    'minus': '\ue11c',
    'refresh': '\ue145',
    'clipboard': '\ue086',
    'scan': '\ue258',
    'images': '\ue5c4',
    'copy': '\ue09e',
    'send': '\ue152',
    'keyboard': '\ue284',
    'check': '\ue06c',
    'alert': '\ue193',
    'lock': '\ue10b',
    'image': '\ue0f6',
}

# ---------------------------------------------------------------- 配色（iOS 亮色）
BG = [0.957, 0.957, 0.969, 1]        # #F4F4F7 页面底色
CARD = [1, 1, 1, 1]                  # 卡片
BLUE = [0.039, 0.518, 1.0, 1]        # #0A84FF SF 蓝
BLUE_TINT = [0.914, 0.953, 1.0, 1]   # #E9F3FF 淡蓝底
GREEN = [0.204, 0.78, 0.349, 1]      # #34C759
ORANGE = [1.0, 0.584, 0.0, 1]        # #FF9500
RED = [1.0, 0.231, 0.188, 1]         # #FF3B30
LABEL = [0.11, 0.11, 0.118, 1]       # #1C1C1E 主文字
LABEL2 = [0.337, 0.337, 0.361, 1]    # #56565C 次要文字
GRAY = [0.557, 0.557, 0.576, 1]      # #8E8E93 说明文字
SEP = [0.898, 0.898, 0.918, 1]       # #E5E5EA 分隔线
FIELD = [0.949, 0.949, 0.961, 1]     # 输入框底

# ---------------------------------------------------------------- 文案
HELP_TEXT = (
    '【使用步骤】\n'
    '1. 到 platform.deepseek.com 注册并创建 API Key，在「API 设置」里填入'
    '（识图和解题默认都用 deepseek-flash，也可以换成别的可用模型；'
    '同一个 Key 即可）。\n\n'
    '2. 点「① 申请悬浮窗权限」，在系统设置里允许本应用显示在其他应用上层。\n\n'
    '3. 搜题方式（三选一，可在「API 设置」里改悬浮球点按动作）：\n'
    '   【最新截图】推荐，最省事：先按手机截图快捷键把题目截图'
    '（一般是电源键+音量下，或三指下滑），再点悬浮球，'
    'App 会读相册里最新那张截图去搜题。首次使用需允许读取图片。\n'
    '   【截图搜题】点悬浮球自动截屏。点「② 开启截屏授权」后按系统提示'
    '选择要共享的应用；授权成功后悬浮球会自动出现。完全杀掉 App 或锁屏后，'
    '系统会回收授权，需要回来重新点一次「②」。\n'
    '   【剪贴板搜题】长按选中题目文字 → 复制，再点面板里的【读剪贴板】。\n\n'
    '4. 截图授权成功后悬浮球会自动出现；如果选用「最新截图」或'
    '「剪贴板搜题」，也可点「③ 启动悬浮球」并自动退到后台。然后打开佛脚刷题。\n'
    '   · 点一下悬浮球 = 按设置的动作搜题\n'
    '   · 拖动悬浮球 = 移动位置\n\n'
    '5. 答案会显示在悬浮面板里，可拖动、最小化、复制。\n\n'
    '【关于「隐私应用」受限（重要）】\n'
    'Android 14 起，截屏授权里可以选「共享一个应用」或「共享整个屏幕」。\n'
    '· 选「共享一个应用」时，微信、银行、支付类应用可能被系统判定为隐私应用'
    '而受限：按系统弹窗提示**手动解除限制**，或者干脆选「共享整个屏幕」。\n'
    '· 这种模式下**只有被选中的应用在前台时才截得到画面**，所以在别的界面点'
    '悬浮球会截到空画面（全黑）。要搜题就在那个 App 的界面里点悬浮球；'
    '不想受限制就用「共享整个屏幕」。\n'
    '· 如果目标应用自己禁止被截屏（系统隐私保护），任何截屏方式都拿不到画面，'
    '那就只能用【读剪贴板】：长按选中题目文字 → 复制 → 回面板点【读剪贴板】。\n\n'
    '【省电与自启动（国产系统常见）】\n'
    '· 设置 → 电池 → 后台耗电管理 → 允许本应用后台高耗电\n'
    '· 设置 → 应用 → 自启动 → 打开\n'
    '· 不要从最近任务里划掉本应用，否则悬浮球与截屏授权会失效\n\n'
    '本工具仅供个人学习研究，请遵守题目来源平台的规则。'
)

#: 主界面提示卡片的短文案（点开看完整说明）
HINT_SHORT = ('部分应用（如微信）会被系统判定为「隐私应用」而限制截屏，'
              '需要手动解除限制')
HINT_FULL_TITLE = '共享范围与隐私应用'
HINT_FULL = (
    'Android 14 起，截屏授权弹窗里可以选「共享一个应用」或「共享整个屏幕」。\n\n'
    '① 选应用时被限制/灰掉\n'
    '微信、银行等应用可能被系统判定为隐私应用而受限。按系统弹窗里的提示'
    '手动解除限制即可；也可以直接改选「共享整个屏幕」——整屏共享不受'
    '单个应用的隐私限制。\n\n'
    '② 截出来的画面是空的（全黑）\n'
    '常见原因有两个：\n'
    '· 选了「共享一个应用」——这种模式下只有那个应用在前台时才截得到画面，'
    '所以要**在要搜题的 App 界面里点悬浮球**；不想受这个限制就改选「共享整个屏幕」。\n'
    '· 该应用自己禁止被截屏（系统隐私保护，FLAG_SECURE），'
    '这种情况任何截屏方式都拿不到画面。\n'
    '两种情况都可以改用【读剪贴板】：在题目界面长按选中题目文字 → 复制 → '
    '回到面板点【读剪贴板】。\n\n'
    '③ 锁屏或切后台后失效\n'
    '系统会自动回收截屏授权，回 App 重新点一次「② 开启截屏授权」。'
)


def _register_fonts():
    """注册中文字体与图标字体，返回是否拿到图标字体。

    Kivy 默认字体 Roboto 不含汉字（会显示成方框），注册名用 'Roboto'
    即可让所有控件全局生效。图标字体注册为 'Lucide'，只在 IconLabel 上用。
    """
    ok_cjk = False
    candidates = [FONT_FILE, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), FONT_FILE)]
    for path in candidates:
        try:
            if os.path.exists(path):
                LabelBase.register(name='Roboto', fn_regular=path)
                ok_cjk = True
                break
        except Exception:
            traceback.print_exc()
    if not ok_cjk:
        print('警告: 未找到中文字体 %s，汉字可能显示为方框' % FONT_FILE)

    icons = [ICON_FILE, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ICON_FILE)]
    for path in icons:
        try:
            if os.path.exists(path):
                LabelBase.register(name='Lucide', fn_regular=path)
                return True
        except Exception:
            traceback.print_exc()
    print('提示: 未找到图标字体 %s，本次不显示图标' % ICON_FILE)
    return False


#: 在导入期就注册（图标是否可用决定 ICON 的取值，类属性要用到）
HAS_ICONS = _register_fonts()
if not HAS_ICONS:
    ICON = dict((k, '') for k in ICON)

#: 兼容旧名字
_register_cjk_font = _register_fonts

# 思考强度：界面文字 ↔ DeepSeek API 取值（low / high / max）
EFFORT_VALUE = {'关闭': 'off', '低': 'low', '高': 'high', '最高': 'max'}
EFFORT_LABEL = {v: k for k, v in EFFORT_VALUE.items()}

# 悬浮球点按动作：界面文字 ↔ bridge 内部取值
TAP_VALUE = {'截图搜题': 'screenshot', '最新截图': 'shot', '剪贴板搜题': 'clipboard'}


# ---------------------------------------------------------------- 基础控件
class SoftCard(BoxLayout):
    """白色圆角卡片 + 柔和阴影 + 极细描边。"""
    radius = NumericProperty(16)

    def __init__(self, **kw):
        kw.setdefault('orientation', 'vertical')
        super().__init__(**kw)
        self._layers = []
        with self.canvas.before:
            # 三层不同透明度/外扩，叠出柔和边缘（比 FBO 阴影便宜且兼容）
            for grow, alpha in ((6.0, 0.028), (4.0, 0.034), (2.0, 0.042)):
                col = Color(0, 0, 0, alpha)
                rect = RoundedRectangle()
                self._layers.append((col, rect, grow))
            self._bgc = Color(*CARD)
            self._bg = RoundedRectangle()
            self._linec = Color(*SEP)
            self._line = Line(width=dp(0.6))
        self.bind(pos=self._redraw, size=self._redraw, radius=self._redraw)
        self._redraw()

    def _redraw(self, *_a):
        x, y = self.pos
        w, h = self.size
        for _col, rect, grow in self._layers:
            g = dp(grow)
            rect.pos = (x - g * 0.5, y - g * 0.5 - dp(0.5))
            rect.size = (w + g, h + g)
            rect.radius = [self.radius + g * 0.5]
        self._bg.pos = (x, y)
        self._bg.size = (w, h)
        self._bg.radius = [self.radius]
        self._line.rounded_rectangle = (x, y, w, h, self.radius)


class IconLabel(Label):
    """图标标签：单独用 Lucide 字体（不能和汉字混排）。"""
    def __init__(self, **kw):
        kw.setdefault('font_name', 'Lucide')
        kw.setdefault('halign', 'center')
        kw.setdefault('valign', 'middle')
        super().__init__(**kw)


class Loader(Widget):
    """iOS 风格的八段加载指示器。

    八个圆角小段绕中心排一圈，靠相位差做"追光" —— 和 uiverse.io 上那类
    iOS spinner 是同一个观感。纯 canvas 绘制：手机上走 GPU 合成，
    不依赖图片素材，也不用改 buildozer.spec 的打包扩展名。
    """
    color = ListProperty(BLUE)
    interval = NumericProperty(0.09)

    def __init__(self, **kw):
        super().__init__(**kw)
        self._bars = []
        self._phase = 0
        self._ev = None
        with self.canvas:
            for _i in range(8):
                col = Color(*self.color)
                PushMatrix()
                rot = Rotate(angle=0, origin=(0, 0))
                bar = RoundedRectangle()
                PopMatrix()
                self._bars.append((col, rot, bar))
        self.bind(pos=self._redraw, size=self._redraw, color=self._recolor)
        self._redraw()
        self._ev = Clock.schedule_interval(self._step, self.interval)

    def _recolor(self, *_a):
        self._step(0)

    def _redraw(self, *_a):
        x, y = self.pos
        w, h = self.size
        if w <= 1 or h <= 1:
            return
        cx, cy = x + w / 2.0, y + h / 2.0
        bar_w = max(dp(1.6), w * 0.17)
        bar_h = max(dp(3.0), h * 0.28)
        inner = min(w, h) * 0.5 - bar_h
        for i, (_col, rot, bar) in enumerate(self._bars):
            rot.origin = (cx, cy)
            rot.angle = i * 45.0
            bar.pos = (cx - bar_w / 2.0, cy + inner)
            bar.size = (bar_w, bar_h)
            bar.radius = [bar_w / 2.0]

    def _step(self, _dt):
        self._phase = (self._phase + 1) % 8
        n = len(self._bars)
        for i, (col, _rot, _bar) in enumerate(self._bars):
            k = (i - self._phase) % n
            a = 1.0 - (k / float(n)) * 0.86
            col.rgba = (self.color[0], self.color[1], self.color[2], a)


class Pill(Button):
    """圆角按钮：filled(实心蓝) / tinted(淡蓝底) / gray / plain / destructive"""
    kind = OptionProperty('filled', options=['filled', 'tinted', 'gray',
                                             'plain', 'destructive'])
    radius = NumericProperty(12)

    def __init__(self, **kw):
        kw.setdefault('background_normal', '')
        kw.setdefault('background_down', '')
        kw.setdefault('background_color', (0, 0, 0, 0))
        super().__init__(**kw)
        with self.canvas.before:
            self._c = Color(*BLUE)
            self._r = RoundedRectangle()
        self.bind(pos=self._redraw, size=self._redraw, state=self._on_state,
                  kind=self._apply_kind)
        self._apply_kind()
        self._redraw()

    def _apply_kind(self, *_a):
        if self.kind == 'filled':
            self._c.rgba = BLUE
            self.color = [1, 1, 1, 1]
        elif self.kind == 'tinted':
            self._c.rgba = BLUE_TINT
            self.color = BLUE
        elif self.kind == 'gray':
            self._c.rgba = [0.918, 0.918, 0.933, 1]
            self.color = LABEL
        elif self.kind == 'destructive':
            self._c.rgba = [1, 1, 1, 0]
            self.color = RED
        else:  # plain
            self._c.rgba = [1, 1, 1, 0]
            self.color = BLUE

    def _redraw(self, *_a):
        self._r.pos = self.pos
        self._r.size = self.size
        self._r.radius = [self.radius]

    def _on_state(self, *_a):
        Animation.cancel_all(self, 'opacity')
        if self.state == 'down':
            Animation(opacity=0.6, duration=0.05).start(self)
        else:
            Animation(opacity=1.0, duration=0.16).start(self)


class IconButton(ButtonBehavior, Widget):
    """只有一个图标的可点区域（比如左上角返回箭头）。"""
    glyph = StringProperty('')
    color = ListProperty(BLUE)
    size_pt = NumericProperty(20)

    def __init__(self, **kw):
        super().__init__(**kw)
        self._lb = IconLabel(text=self.glyph, font_size=sp(self.size_pt),
                             color=self.color)
        self.add_widget(self._lb)
        self.bind(pos=self._redraw, size=self._redraw, glyph=self._on_glyph)
        self._redraw()

    def _on_glyph(self, *_a):
        self._lb.text = self.glyph

    def _redraw(self, *_a):
        self._lb.pos = self.pos
        self._lb.size = self.size

    def on_state(self, *_a):
        Animation.cancel_all(self, 'opacity')
        Animation(opacity=0.55 if self.state == 'down' else 1.0,
                  duration=0.08).start(self)


class ListRow(ButtonBehavior, BoxLayout):
    """一行：序号徽标? + 图标 + 标题 + （KV 追加的值/控件） + 自绘箭头 + 分隔线。

    注意实现顺序：内置控件在 __init__ 里 add_widget，KV 里声明的子控件会在
    之后追加，于是天然排在内置控件右边（正好是"值/加载指示器"的位置）；
    箭头和分隔线不用子控件、直接用 canvas 画在行内，避免依赖插入顺序
    （on_kv_post 在 KV 里创建的子控件上不一定会被调用，这里不用它）。
    """
    title = StringProperty('')
    icon = StringProperty('')
    icon_color = ListProperty(BLUE)
    badge = StringProperty('')
    badge_color = ListProperty(BLUE)
    show_chevron = BooleanProperty(False)
    divider = BooleanProperty(True)
    title_size = NumericProperty(15)

    def __init__(self, **kw):
        kw.setdefault('orientation', 'horizontal')
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(50))
        kw.setdefault('padding', [dp(14), dp(4), dp(12), dp(4)])
        kw.setdefault('spacing', dp(10))
        super().__init__(**kw)
        # 前置元素永远只有一个 22dp 的"图标格子"：有编号就在格子里画圆徽，
        # 否则就放图标。这样所有卡片的标题列都在同一条竖线上。
        with self.canvas.before:
            self._bc = Color(*BLUE)
            self._be = Ellipse(size=(0, 0))
        self._icon = IconLabel(text=self.icon, font_size=sp(17),
                               color=self.icon_color, size_hint_x=None,
                               width=dp(22))
        self.add_widget(self._icon)
        self._title = Label(text=self.title, font_size=sp(self.title_size),
                            color=LABEL, halign='left', valign='middle',
                            size_hint_x=1)
        self._title.bind(size=self._title_size)
        self.add_widget(self._title)
        with self.canvas.after:
            self._dc = Color(*SEP)
            self._dl = Line(width=dp(0.6))
            self._cc = Color(*GRAY)
            self._cl = Line(width=dp(1.9), cap='round', joint='round')
        # KV 里的 title/icon/badge 是构造之后才赋值的，必须绑上
        self.bind(pos=self._redraw, size=self._redraw,
                  title=self._sync, icon=self._sync, badge=self._sync,
                  icon_color=self._sync, badge_color=self._sync,
                  title_size=self._sync, show_chevron=self._sync)
        self._sync()
        self._redraw()

    def _sync(self, *_a):
        self._title.text = self.title
        self._title.font_size = sp(self.title_size)
        self._title_size()
        if self.badge:
            # 编号用中文字体（Lucide 是图标字体，没有数字）
            self._icon.font_name = 'Roboto'
            self._icon.text = self.badge
            self._icon.color = [1, 1, 1, 1]
            self._icon.bold = True
            self._icon.font_size = sp(12)
        else:
            self._icon.font_name = 'Lucide'
            self._icon.text = self.icon
            self._icon.color = self.icon_color
            self._icon.bold = False
            self._icon.font_size = sp(17)
        # 有箭头的行右边留出位置，别让箭头压到文字
        self.padding = [dp(14), dp(4),
                        dp(26) if self.show_chevron else dp(12), dp(4)]
        self._redraw()

    def _title_size(self, *_a):
        self._title.text_size = (self._title.width, self._title.height)

    def _redraw(self, *_a):
        x, y = self.pos
        w, h = self.size
        # 编号圆徽画在图标格子中心（跟着 padding 走，所以左边距变了也跟得上）
        if self.badge and h > 0:
            d = dp(22)
            icx = x + self.padding[0] + d / 2.0
            icy = y + h / 2.0
            self._be.pos = (icx - d / 2.0, icy - d / 2.0)
            self._be.size = (d, d)
            self._bc.rgba = self.badge_color
        else:
            self._be.size = (0, 0)
        self._dc.a = SEP[3] if self.divider else 0
        self._dl.points = [x + dp(14), y, x + w, y]
        if self.show_chevron:
            cx, cy = x + w - dp(12), y + h / 2.0
            dx, dy = dp(3.4), dp(5.2)
            self._cl.points = [cx - dx, cy + dy, cx + dx, cy, cx - dx, cy - dy]
        else:
            self._cl.points = []

    def on_state(self, *_a):
        Animation.cancel_all(self, 'opacity')
        if self.state == 'down':
            Animation(opacity=0.6, duration=0.05).start(self)
        else:
            Animation(opacity=1.0, duration=0.16).start(self)



class SectionHeader(Label):
    def __init__(self, **kw):
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(26))
        kw.setdefault('font_size', sp(13))
        kw.setdefault('color', GRAY)
        kw.setdefault('halign', 'left')
        kw.setdefault('valign', 'middle')
        kw.setdefault('padding', [dp(6), 0, 0, 0])
        super().__init__(**kw)
        self.bind(size=self._fix,
                  texture_size=self._fix)

    def _fix(self, *_a):
        self.text_size = self.size


class Field(TextInput):
    """iOS 风格的浅灰圆角输入框。"""
    def __init__(self, **kw):
        kw.setdefault('background_normal', '')
        kw.setdefault('background_active', '')
        kw.setdefault('background_color', (0, 0, 0, 0))
        kw.setdefault('foreground_color', LABEL)
        kw.setdefault('cursor_color', BLUE)
        kw.setdefault('hint_text_color', [0.6, 0.6, 0.62, 1])
        kw.setdefault('font_size', sp(15))
        kw.setdefault('padding', [dp(12), dp(10), dp(12), dp(10)])
        kw.setdefault('size_hint_y', None)
        kw.setdefault('height', dp(44))
        kw.setdefault('write_tab', False)
        super().__init__(**kw)
        with self.canvas.before:
            self._c = Color(*FIELD)
            self._r = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _redraw(self, *_a):
        self._r.pos = self.pos
        self._r.size = self.size


class Alert(ModalView):
    """iOS 风格弹窗：全屏压暗 + 白色圆角卡片 + 蓝色按钮。"""
    title = StringProperty('')
    message = StringProperty('')
    ok_text = StringProperty('好')

    def __init__(self, **kw):
        kw.setdefault('size_hint', (1, 1))
        kw.setdefault('background', '')
        kw.setdefault('background_color', (0, 0, 0, 0))
        kw.setdefault('auto_dismiss', True)
        super().__init__(**kw)
        with self.canvas.before:
            self._dim = Color(0, 0, 0, 0.28)
            self._dimr = RoundedRectangle()
        self.bind(pos=self._redraw, size=self._redraw)
        self._build()
        self._redraw()

    def _redraw(self, *_a):
        self._dimr.pos = self.pos
        self._dimr.size = self.size
        self._dimr.radius = [0]

    def _build(self):
        card = SoftCard(size_hint=(0.82, None),
                        padding=[dp(18), dp(18), dp(18), dp(12)],
                        spacing=dp(10))
        card.bind(minimum_height=card.setter('height'))
        card.add_widget(Label(text=self.title or '提示', bold=True,
                              font_size=sp(17), color=LABEL,
                              size_hint_y=None, height=dp(28)))
        msg = Label(text=self.message, font_size=sp(14), color=LABEL2,
                    halign='left', valign='top', size_hint_y=None,
                    height=dp(60))
        msg.bind(width=lambda *a: setattr(msg, 'text_size', (msg.width, None)),
                 texture_size=lambda *a: setattr(
                     msg, 'height', max(dp(24), msg.texture_size[1] + dp(6))))
        card.add_widget(msg)
        btn = Pill(text=self.ok_text, kind='tinted', size_hint_y=None,
                   height=dp(44), bold=True)
        btn.bind(on_release=lambda *a: self.dismiss())
        card.add_widget(btn)
        self.add_widget(card)


class ActionSheet(ModalView):
    """iOS 风格的底部/居中选择列表（用于拉取到的模型列表）。"""
    def __init__(self, title, items, on_pick, **kw):
        kw.setdefault('size_hint', (1, 1))
        kw.setdefault('background', '')
        kw.setdefault('background_color', (0, 0, 0, 0))
        super().__init__(**kw)
        with self.canvas.before:
            self._dim = Color(0, 0, 0, 0.25)
            self._dimr = RoundedRectangle()
        self.bind(pos=self._redraw, size=self._redraw)

        box = BoxLayout(orientation='vertical', size_hint=(0.86, 0.7),
                        spacing=dp(8), pos_hint={'center_x': 0.5,
                                                 'center_y': 0.5})
        card = SoftCard(padding=[dp(16), dp(12), dp(16), dp(14)],
                        spacing=dp(8))
        card.add_widget(Label(text=title, bold=True, font_size=sp(16),
                              color=LABEL, size_hint_y=None, height=dp(28)))
        grid = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        for it in items:
            b = Pill(text=it, kind='tinted', size_hint_y=None, height=dp(44))
            b.bind(on_release=(lambda name: lambda *a: (
                self.dismiss(), on_pick(name)))(it))
            grid.add_widget(b)
        sv = ScrollView(do_scroll_x=False)
        sv.add_widget(grid)
        card.add_widget(sv)
        box.add_widget(card)

        cancel = SoftCard(size_hint_y=None, height=dp(54),
                          padding=[dp(8), dp(5), dp(8), dp(5)])
        cb = Pill(text='取消', kind='plain', bold=True, size_hint_y=None,
                  height=dp(44))
        cb.bind(on_release=lambda *a: self.dismiss())
        cancel.add_widget(cb)
        box.add_widget(cancel)

        self.add_widget(box)
        self._redraw()

    def _redraw(self, *_a):
        self._dimr.pos = self.pos
        self._dimr.size = self.size
        self._dimr.radius = [0]


# ---------------------------------------------------------------- 界面
KV = '''
#:import SlideTransition kivy.uix.screenmanager.SlideTransition

# 设计令牌（与 main.py 顶部常量保持一致）
#:set BG 0.957, 0.957, 0.969, 1
#:set BLUE 0.039, 0.518, 1, 1
#:set GREEN 0.204, 0.78, 0.349, 1
#:set ORANGE 1, 0.584, 0, 1
#:set RED 1, 0.231, 0.188, 1
#:set LABEL 0.11, 0.11, 0.118, 1
#:set LABEL2 0.337, 0.337, 0.361, 1
#:set GRAY 0.557, 0.557, 0.576, 1

<SpinnerOption>:
    background_normal: ''
    background_down: ''
    background_color: 1, 1, 1, 1
    color: BLUE
    font_size: sp(15)

<MainScreen>:
    name: 'main'
    canvas.before:
        Color:
            rgba: BG
        Rectangle:
            pos: self.pos
            size: self.size
    ScrollView:
        do_scroll_x: False
        bar_width: 0
        BoxLayout:
            orientation: 'vertical'
            size_hint_y: None
            height: self.minimum_height
            padding: dp(16), dp(12), dp(16), dp(24)
            spacing: dp(12)

            # ---------- 标题 ----------
            BoxLayout:
                size_hint_y: None
                height: dp(54)
                orientation: 'vertical'
                Label:
                    text: '佛脚AI搜题'
                    bold: True
                    font_size: sp(23)
                    color: LABEL
                    halign: 'left'
                    valign: 'bottom'
                    text_size: self.size
                Label:
                    text: '悬浮球 + DeepSeek 搜题助手'
                    font_size: sp(12)
                    color: GRAY
                    halign: 'left'
                    valign: 'top'
                    text_size: self.size

            # ---------- 三步流程 ----------
            SoftCard:
                size_hint_y: None
                height: self.minimum_height
                padding: dp(4), dp(6), dp(4), dp(6)
                ListRow:
                    id: row_step1
                    badge: '1'
                    title: '申请悬浮窗权限'
                    on_release: app.req_overlay()
                ListRow:
                    id: row_step2
                    badge: '2'
                    title: '开启截屏授权'
                    on_release: app.req_projection()
                ListRow:
                    id: row_step3
                    badge: '3'
                    badge_color: GREEN
                    title: '启动悬浮球，去刷题'
                    divider: False
                    on_release: app.start_ball()

            # ---------- 隐私应用提示 ----------
            SoftCard:
                size_hint_y: None
                height: dp(64)
                padding: dp(4), dp(4), dp(4), dp(4)
                ListRow:
                    title: app.hint_short
                    title_size: 12
                    icon: app.icon_info
                    icon_color: ORANGE
                    show_chevron: True
                    divider: False
                    on_release: app.show_hint()

            # ---------- 运行状态 ----------
            SoftCard:
                size_hint_y: None
                height: self.minimum_height
                padding: dp(4), dp(4), dp(4), dp(4)
                ListRow:
                    title: '悬浮窗权限'
                    icon: app.icon_shield
                    divider: True
                    Label:
                        text: app.st_overlay_text
                        font_size: sp(14)
                        color: app.st_overlay_color
                        size_hint_x: 0.34
                        halign: 'right'
                        valign: 'middle'
                        text_size: self.size
                ListRow:
                    title: '截屏授权'
                    icon: app.icon_screenshare
                    divider: True
                    Label:
                        text: app.st_proj_text
                        font_size: sp(14)
                        color: app.st_proj_color
                        size_hint_x: 0.34
                        halign: 'right'
                        valign: 'middle'
                        text_size: self.size
                ListRow:
                    title: 'API Key'
                    icon: app.icon_key
                    divider: True
                    Label:
                        text: app.st_key_text
                        font_size: sp(14)
                        color: app.st_key_color
                        size_hint_x: 0.34
                        halign: 'right'
                        valign: 'middle'
                        text_size: self.size
                ListRow:
                    title: '当前模型'
                    icon: app.icon_cpu
                    divider: False
                    Label:
                        text: app.st_model_text
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_x: 0.68
                        halign: 'right'
                        valign: 'middle'
                        text_size: self.size
                        # 模型名可能很长（密度高的手机上会被折成两行），
                        # 这里限一行、超出从右边省略（保留开头的模型名）
                        max_lines: 1
                        shorten: True
                        shorten_from: 'right'
                # 兼容旧脚本（_local_run.py 读 ids.status.text），不可见
                Label:
                    id: status
                    text: ''
                    opacity: 0
                    size_hint_y: None
                    height: 0

            # ---------- 快捷与更多 ----------
            SoftCard:
                size_hint_y: None
                height: self.minimum_height
                padding: dp(4), dp(4), dp(4), dp(4)
                ListRow:
                    title: '停止悬浮球'
                    icon: app.icon_square
                    icon_color: RED
                    divider: True
                    on_release: app.stop_ball()
                ListRow:
                    title: '测试 API 连通'
                    icon: app.icon_refresh
                    show_chevron: True
                    divider: True
                    on_release: app.test_api()
                ListRow:
                    title: 'API 设置'
                    icon: app.icon_settings
                    show_chevron: True
                    divider: True
                    on_release: app.sm.current = 'settings'
                ListRow:
                    title: '使用说明'
                    icon: app.icon_book
                    show_chevron: True
                    divider: False
                    on_release: app.sm.current = 'help'

<SettingsScreen>:
    name: 'settings'
    canvas.before:
        Color:
            rgba: BG
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        BoxLayout:
            size_hint_y: None
            height: dp(54)
            padding: dp(8), 0, dp(14), 0
            IconButton:
                glyph: app.icon_back
                size_hint_x: None
                width: dp(40)
                on_release: app.sm.current = 'main'
            Label:
                text: 'API 设置'
                bold: True
                font_size: sp(19)
                color: LABEL
                halign: 'left'
                valign: 'middle'
                text_size: self.size
        ScrollView:
            do_scroll_x: False
            bar_width: 0
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16), dp(2), dp(16), dp(24)
                spacing: dp(8)

                SectionHeader:
                    text: '接口'
                SoftCard:
                    size_hint_y: None
                    height: self.minimum_height
                    padding: dp(14), dp(12), dp(14), dp(14)
                    spacing: dp(8)
                    Label:
                        text: 'API Key（platform.deepseek.com → API Keys 创建）'
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_y: None
                        height: dp(20)
                        halign: 'left'
                        text_size: self.size
                    Field:
                        id: key
                        multiline: False
                        password: True
                        hint_text: 'sk-...'
                    Label:
                        text: 'API Base URL（默认官方地址，一般不改）'
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_y: None
                        height: dp(20)
                        halign: 'left'
                        text_size: self.size
                    Field:
                        id: base
                        multiline: False

                SectionHeader:
                    text: '模型'
                SoftCard:
                    size_hint_y: None
                    height: self.minimum_height
                    padding: dp(14), dp(12), dp(14), dp(14)
                    spacing: dp(8)
                    Label:
                        text: '识图模型（截图识别用）'
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_y: None
                        height: dp(20)
                        halign: 'left'
                        text_size: self.size
                    Field:
                        id: vmodel
                        multiline: False
                    Pill:
                        text: '拉取可用模型列表'
                        kind: 'tinted'
                        size_hint_y: None
                        height: dp(42)
                        on_release: app.fetch_models('vmodel')
                    Label:
                        text: '解题模型（默认 deepseek-flash，也可自己输入）'
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_y: None
                        height: dp(20)
                        halign: 'left'
                        text_size: self.size
                    Field:
                        id: smodel
                        multiline: False
                    Pill:
                        text: '拉取可用模型列表'
                        kind: 'tinted'
                        size_hint_y: None
                        height: dp(42)
                        on_release: app.fetch_models('smodel')

                SectionHeader:
                    text: '生成参数'
                SoftCard:
                    size_hint_y: None
                    height: self.minimum_height
                    padding: dp(4), dp(4), dp(4), dp(4)
                    ListRow:
                        title: '思考强度'
                        icon: app.icon_sparkles
                        Spinner:
                            id: effort
                            text: '高'
                            values: ['关闭', '低', '高', '最高']
                            option_cls: 'SpinnerOption'
                            background_normal: ''
                            background_down: ''
                            background_color: 0, 0, 0, 0
                            color: BLUE
                            font_size: sp(15)
                            size_hint_x: None
                            width: dp(92)
                    ListRow:
                        title: '悬浮球点按动作'
                        icon: app.icon_search
                        divider: False
                        Spinner:
                            id: action
                            text: '截图搜题'
                            values: ['截图搜题', '最新截图', '剪贴板搜题']
                            option_cls: 'SpinnerOption'
                            background_normal: ''
                            background_down: ''
                            background_color: 0, 0, 0, 0
                            color: BLUE
                            font_size: sp(15)
                            size_hint_x: None
                            width: dp(116)

                SectionHeader:
                    text: '提示词'
                SoftCard:
                    size_hint_y: None
                    height: self.minimum_height
                    padding: dp(14), dp(12), dp(14), dp(14)
                    spacing: dp(8)
                    Label:
                        text: '解题提示词（系统提示词，留空用内置默认）'
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_y: None
                        height: dp(20)
                        halign: 'left'
                        text_size: self.size
                    Field:
                        id: sprompt
                        multiline: True
                        height: dp(140)
                        font_size: sp(12)
                    Label:
                        text: '识图提示词（截图识别用，留空用内置默认）'
                        font_size: sp(13)
                        color: LABEL2
                        size_hint_y: None
                        height: dp(20)
                        halign: 'left'
                        text_size: self.size
                    Field:
                        id: vprompt
                        multiline: True
                        height: dp(104)
                        font_size: sp(12)
                    Pill:
                        text: '恢复默认提示词'
                        kind: 'tinted'
                        size_hint_y: None
                        height: dp(42)
                        on_release: app.reset_prompts()

                Pill:
                    text: '保存并返回'
                    kind: 'filled'
                    bold: True
                    size_hint_y: None
                    height: dp(48)
                    on_release: app.save_settings()
                Pill:
                    text: '返回（不保存）'
                    kind: 'plain'
                    size_hint_y: None
                    height: dp(44)
                    on_release: app.sm.current = 'main'

<HelpScreen>:
    name: 'help'
    canvas.before:
        Color:
            rgba: BG
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        BoxLayout:
            size_hint_y: None
            height: dp(54)
            padding: dp(8), 0, dp(14), 0
            IconButton:
                glyph: app.icon_back
                size_hint_x: None
                width: dp(40)
                on_release: app.sm.current = 'main'
            Label:
                text: '使用说明'
                bold: True
                font_size: sp(19)
                color: LABEL
                halign: 'left'
                valign: 'middle'
                text_size: self.size
        ScrollView:
            do_scroll_x: False
            bar_width: 0
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16), dp(2), dp(16), dp(20)
                SoftCard:
                    size_hint_y: None
                    height: self.minimum_height + dp(28)
                    padding: dp(16), dp(14), dp(16), dp(14)
                    Label:
                        text: root.help_text
                        size_hint_y: None
                        height: self.texture_size[1]
                        text_size: self.width, None
                        halign: 'left'
                        valign: 'top'
                        font_size: sp(14)
                        color: LABEL2

<DesktopScreen>:
    name: 'desktop'
    canvas.before:
        Color:
            rgba: BG
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        BoxLayout:
            size_hint_y: None
            height: dp(54)
            padding: dp(16), 0, dp(16), 0
            Label:
                text: '桌面调试模式'
                bold: True
                font_size: sp(19)
                color: LABEL
                halign: 'left'
                valign: 'middle'
                text_size: self.size
        ScrollView:
            do_scroll_x: False
            bar_width: 0
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16), dp(2), dp(16), dp(20)
                spacing: dp(10)
                SoftCard:
                    size_hint_y: None
                    height: self.minimum_height
                    padding: dp(14), dp(12), dp(14), dp(14)
                    spacing: dp(8)
                    Field:
                        id: dkey
                        hint_text: 'DeepSeek API Key'
                        multiline: False
                        password: True
                    Field:
                        id: dquestion
                        hint_text: '输入题目文字…'
                        multiline: True
                        height: dp(90)
                    Field:
                        id: dimage
                        hint_text: '或填本地截图路径（jpg/png），优先使用'
                        multiline: False
                    Pill:
                        text: '提问'
                        kind: 'filled'
                        bold: True
                        size_hint_y: None
                        height: dp(46)
                        on_release: app.desktop_ask()
                SoftCard:
                    size_hint_y: None
                    height: max(self.minimum_height, dp(170))
                    padding: dp(14), dp(12), dp(14), dp(12)
                    Label:
                        id: dresult
                        text: '结果会显示在这里'
                        size_hint_y: None
                        height: max(self.texture_size[1], dp(140))
                        text_size: self.width, None
                        halign: 'left'
                        valign: 'top'
                        font_size: sp(14)
                        color: LABEL2

ScreenManager:
    transition: SlideTransition(duration=0.24, direction='left')
    MainScreen:
    SettingsScreen:
    HelpScreen:
    DesktopScreen:
'''


class MainScreen(Screen):
    pass


class SettingsScreen(Screen):
    pass


class HelpScreen(Screen):
    help_text = StringProperty(HELP_TEXT)


class DesktopScreen(Screen):
    pass


class FojiaoApp(App):
    title = '佛脚AI搜题'

    # ---- 状态文案（绑到主界面各行右侧）----
    st_overlay_text = StringProperty('—')
    st_overlay_color = ListProperty(GRAY)
    st_proj_text = StringProperty('—')
    st_proj_color = ListProperty(GRAY)
    st_key_text = StringProperty('—')
    st_key_color = ListProperty(GRAY)
    st_model_text = StringProperty('—')

    # ---- 图标（KV 里通过 app.icon_xxx 使用）----
    icon_search = StringProperty(ICON['search'])
    icon_shield = StringProperty(ICON['shield'])
    icon_screenshare = StringProperty(ICON['screenshare'])
    icon_play = StringProperty(ICON['play'])
    icon_square = StringProperty(ICON['square'])
    icon_refresh = StringProperty(ICON['refresh'])
    icon_settings = StringProperty(ICON['settings'])
    icon_book = StringProperty(ICON['book'])
    icon_info = StringProperty(ICON['info'])
    icon_key = StringProperty(ICON['key'])
    icon_cpu = StringProperty(ICON['cpu'])
    icon_sparkles = StringProperty(ICON['sparkles'])
    icon_back = StringProperty(ICON['chevron_left'])
    hint_short = StringProperty(HINT_SHORT)

    #: 每次进入 App 只在第一次点②时提示一次共享范围的事
    _proj_hint_shown = False

    def build(self):
        self.store = JsonStore(os.path.join(self.user_data_dir, 'settings.json'))
        self.sm = Builder.load_string(KV)
        self.load_settings()
        if not IS_ANDROID:
            self.sm.current = 'desktop'
        for s in self.sm.screens:
            s.bind(on_pre_enter=self._fade_in)
        # 接管安卓返回键，否则按返回会直接把应用切到后台
        Window.bind(on_keyboard=self._on_key)
        Clock.schedule_interval(self.refresh_status, 3)
        return self.sm

    def _fade_in(self, screen):
        """页面进入时淡入，切换更顺。"""
        screen.opacity = 0
        Animation(opacity=1, duration=0.22).start(screen)

    def _on_key(self, window, key, *largs):
        """拦截安卓返回键（Python 侧表现为 key == 27）。

        Kivy 默认是 mActivity.moveTaskToBack(True)（直接切后台），
        这里改成：先关弹窗 → 再回主界面 → 只有本来就在主界面才交给系统。
        """
        if not IS_ANDROID or key != 27:
            return False
        try:
            for child in list(Window.children):
                if isinstance(child, ModalView):
                    child.dismiss()
                    return True
        except Exception:
            traceback.print_exc()
        if self.sm.current != 'main':
            self.sm.current = 'main'
            return True
        return False

    def on_pause(self):
        # 退到后台（去刷题App）时返回 True，让 Activity 保持存活，
        # 否则悬浮球所属的窗口会随 Activity 一起失效
        Logger.info('[fojiao] on_pause 触发')
        return True

    def on_resume(self):
        """从后台回到 App 时强制重绘（模拟器上偶发黑屏的兜底）。"""
        Logger.info('[fojiao] on_resume 触发, window=%s' % (Window.size,))

        def _refresh(tag):
            def _f(*_a):
                try:
                    Logger.info('[fojiao] 重绘请求 %s' % tag)
                    Window.canvas.ask_update()
                    Window.dispatch('on_resize', *Window.size)
                except Exception:
                    traceback.print_exc()
            return _f
        try:
            _refresh('now')()
            Clock.schedule_once(_refresh('t+0.3'), 0.3)
            Clock.schedule_once(_refresh('t+1.0'), 1.0)
        except Exception:
            traceback.print_exc()

    # ---------- 设置 ----------
    def load_settings(self):
        # JsonStore.put('cfg', **data) 存进去的就是字段字典本身，
        # get('cfg') 直接返回它，没有再套一层 'data'
        try:
            d = dict(self.store.get('cfg'))
        except Exception:
            d = {}
        ai_core.cfg.update({
            'api_key': d.get('api_key', ''),
            # 用 or 而不是 get 的默认值：旧版本可能存过空串，
            # 空模型名/空地址发出去就是 400，必须回退到默认值
            'base_url': d.get('base_url') or ai_core.cfg['base_url'],
            'vision_model': (d.get('vision_model')
                             or ai_core.cfg['vision_model']),
            'solve_model': d.get('solve_model') or ai_core.cfg['solve_model'],
            'think_effort': d.get('think_effort', ai_core.cfg['think_effort']),
            'ocr_prompt': d.get('ocr_prompt', ''),
            'solve_prompt': d.get('solve_prompt', ''),
        })
        self._tap_action = d.get('tap_action', '截图搜题')
        s = self.sm.get_screen('settings')
        s.ids.key.text = ai_core.cfg['api_key']
        s.ids.base.text = ai_core.cfg['base_url']
        s.ids.vmodel.text = ai_core.cfg['vision_model']
        s.ids.smodel.text = ai_core.cfg['solve_model']
        s.ids.effort.text = EFFORT_LABEL.get(ai_core.cfg['think_effort'], '高')
        # 提示词为空时，界面里显示内置默认值，方便用户在此基础上改
        s.ids.sprompt.text = ai_core.cfg['solve_prompt'] or \
            ai_core.DEFAULT_SOLVE_PROMPT
        s.ids.vprompt.text = ai_core.cfg['ocr_prompt'] or \
            ai_core.DEFAULT_OCR_PROMPT
        s.ids.action.text = self._tap_action
        dsk = self.sm.get_screen('desktop')
        dsk.ids.dkey.text = ai_core.cfg['api_key']

    def reset_prompts(self):
        """把两个提示词恢复成内置默认。"""
        s = self.sm.get_screen('settings')
        s.ids.sprompt.text = ai_core.DEFAULT_SOLVE_PROMPT
        s.ids.vprompt.text = ai_core.DEFAULT_OCR_PROMPT
        self.popup('已恢复', '已填入内置默认提示词，点「保存并返回」生效。')

    def save_settings(self):
        s = self.sm.get_screen('settings')
        # 提示词如果和内置默认一模一样，就存空值，这样以后升级默认值能自动跟随
        sprompt = s.ids.sprompt.text.strip()
        vprompt = s.ids.vprompt.text.strip()
        data = {
            'api_key': s.ids.key.text.strip(),
            'base_url': s.ids.base.text.strip() or ai_core.cfg['base_url'],
            'vision_model': (s.ids.vmodel.text.strip()
                             or ai_core.cfg['vision_model']),
            'solve_model': (s.ids.smodel.text.strip()
                            or ai_core.cfg['solve_model']),
            'think_effort': EFFORT_VALUE.get(s.ids.effort.text, 'high'),
            'solve_prompt': '' if sprompt == ai_core.DEFAULT_SOLVE_PROMPT.strip()
                            else sprompt,
            'ocr_prompt': '' if vprompt == ai_core.DEFAULT_OCR_PROMPT.strip()
                          else vprompt,
            'tap_action': s.ids.action.text,
        }
        self.store.put('cfg', **data)
        self.load_settings()
        if bridge:
            bridge.set_cfg({'tap_action': TAP_VALUE.get(data['tap_action'],
                                                       'screenshot')})
        self.popup('已保存', '配置已保存。')

    # ---------- 状态 ----------
    def refresh_status(self, _dt):
        if self.sm.current not in ('main',):
            return
        overlay_ok = proj_ok = None
        if IS_ANDROID and bridge:
            try:
                overlay_ok = bridge.has_overlay_permission()
                proj_ok = bridge.has_projection()
            except Exception:
                traceback.print_exc()

        if overlay_ok is None:
            self.st_overlay_text, self.st_overlay_color = '—', GRAY
        elif overlay_ok:
            self.st_overlay_text, self.st_overlay_color = '已授权', GREEN
        else:
            self.st_overlay_text, self.st_overlay_color = '未授权', ORANGE
        if proj_ok is None:
            self.st_proj_text, self.st_proj_color = '—', GRAY
        elif proj_ok:
            self.st_proj_text, self.st_proj_color = '已授权', GREEN
        else:
            self.st_proj_text, self.st_proj_color = '未授权', ORANGE
        self.st_key_text = '已设置' if ai_core.cfg['api_key'] else '未设置'
        self.st_key_color = GREEN if ai_core.cfg['api_key'] else RED
        self.st_model_text = ai_core.cfg['solve_model']

        # 兼容旧脚本：ids.status 仍然存在（隐藏）
        try:
            lines = ['运行状态',
                     '悬浮窗权限：%s' % self.st_overlay_text,
                     '截屏授权：%s' % self.st_proj_text,
                     'API Key：%s' % self.st_key_text,
                     '模型：%s' % self.st_model_text]
            self.sm.get_screen('main').ids.status.text = '\n'.join(lines)
        except Exception:
            pass

    # ---------- 按钮动作 ----------
    def req_overlay(self):
        if not (IS_ANDROID and bridge):
            self.popup('提示', '仅 Android 端支持（桌面端可在调试页测试）')
            return
        bridge.request_overlay_permission()

    def req_projection(self):
        if not (IS_ANDROID and bridge):
            self.popup('提示', '仅 Android 端支持')
            return
        # 第一次点②时，把"共享范围/隐私应用"的事说清楚（避免选微信时踩坑）
        if not self._proj_hint_shown:
            self._proj_hint_shown = True
            self.popup(HINT_FULL_TITLE, HINT_FULL)
        bridge.request_projection()

    def show_hint(self):
        self.popup(HINT_FULL_TITLE, HINT_FULL)

    def start_ball(self):
        if not (IS_ANDROID and bridge):
            self.popup('提示', '仅 Android 端支持')
            return
        if not bridge.has_overlay_permission():
            self.popup('缺少权限', '请先点「① 申请悬浮窗权限」')
            return
        if not ai_core.cfg['api_key']:
            self.popup('缺少配置', '请先到「API 设置」填写 DeepSeek API Key')
            return
        bridge.set_cfg({'tap_action': TAP_VALUE.get(self._tap_action,
                                                    'screenshot')})
        bridge.show_ball()
        bridge.move_task_to_back()

    def stop_ball(self):
        if bridge:
            bridge.hide_ball()

    def test_api(self):
        """测试连通：用弹窗反馈（测试期间按钮不可重复点）。"""
        if self._busy:
            return
        self._busy = True
        busy = self.popup('测试中…', '正在请求 DeepSeek 接口…')

        def done(msg):
            try:
                busy.dismiss()
            except Exception:
                pass
            self._busy = False
            self.popup('API 测试', msg)

        def worker():
            try:
                msg = ai_core.test_connection()
            except Exception as e:
                msg = '错误： ' + str(e)
            Clock.schedule_once(lambda dt: done(msg), 0)
        threading.Thread(target=worker, daemon=True).start()

    # ---------- 桌面调试 ----------
    def desktop_ask(self):
        dsk = self.sm.get_screen('desktop')
        ai_core.cfg['api_key'] = dsk.ids.dkey.text.strip()
        img = dsk.ids.dimage.text.strip()
        q = dsk.ids.dquestion.text.strip()
        dsk.ids.dresult.text = '请求中…'

        def worker():
            try:
                if img and os.path.exists(img):
                    q2 = ai_core.ocr_question(img)
                    Clock.schedule_once(lambda dt: setattr(
                        dsk.ids.dresult, 'text',
                        '【识别题目】\n' + q2 + '\n\n解答中…'), 0)
                    ans = ai_core.solve_question(q2)
                elif q:
                    ans = ai_core.solve_question(q)
                else:
                    raise RuntimeError('请输入题目或图片路径')
                Clock.schedule_once(lambda dt: setattr(
                    dsk.ids.dresult, 'text',
                    '【解答】\n' + ai_core.plain_text(ans)), 0)
            except Exception as e:
                traceback.print_exc()
                # 注意：不能在延迟执行的 lambda 里引用 e，
                # Python 3 会在 except 块结束时删除该名字
                err = '错误： ' + str(e)
                Clock.schedule_once(lambda dt: setattr(
                    dsk.ids.dresult, 'text', err), 0)
        threading.Thread(target=worker, daemon=True).start()

    # ----------
    def popup(self, title, msg):
        a = Alert(title=title, message=msg)
        a.open()
        return a

    # ---------- 模型列表 ----------
    def fetch_models(self, target):
        """从接口拉取可用模型，弹出列表供点选（失败时提示手动输入）。"""
        if self._busy:
            return
        self._busy = True

        def done(ids=None, err=None):
            self._busy = False
            if err:
                self.popup('获取失败',
                           '%s\n\n可以在模型输入框里手动填写模型名。' % err)
            else:
                self.show_model_picker(target, ids)

        def worker():
            try:
                ids = ai_core.list_models()
                Clock.schedule_once(lambda dt: done(ids=ids), 0)
            except Exception as e:
                traceback.print_exc()
                # 不能在延迟执行的 lambda 里引用 e（except 块结束即失效）
                msg = str(e)
                Clock.schedule_once(lambda dt: done(err=msg), 0)
        threading.Thread(target=worker, daemon=True).start()

    def show_model_picker(self, target, ids):
        """把拉取到的模型列成可点列表，点一下填进对应输入框。"""
        s = self.sm.get_screen('settings')
        which = '识图模型' if target == 'vmodel' else '解题模型'

        def pick(name):
            s.ids[target].text = name
        ActionSheet('点选填入%s' % which, ids, pick).open()

    #: 防止弹窗/请求重入
    _busy = False


if __name__ == '__main__':
    FojiaoApp().run()
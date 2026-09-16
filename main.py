# -*- coding: utf-8 -*-
"""佛脚AI搜题 —— 主程序（Kivy UI）。

Android 端流程：
  1. 设置里填 DeepSeek API Key
  2. 申请悬浮窗权限
  3. 开启截屏授权（每次冷启动后需一次）
  4. 启动悬浮球 → 切到佛脚刷题 → 点悬浮球搜题
桌面端提供简单的调试界面（直接输入题目或本地图片路径测试 API）。
"""
import os
import threading
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.metrics import dp, sp
from kivy.properties import StringProperty
from kivy.storage.jsonstore import JsonStore
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager  # noqa: F401 (KV 里用到 ScreenManager)
from kivy.uix.scrollview import ScrollView
from kivy.utils import platform

import ai_core

try:
    import android_native
    bridge = android_native.bridge
except Exception:
    bridge = None

IS_ANDROID = platform == 'android'

FONT_FILE = 'NotoSansSC-Regular.otf'


def _register_cjk_font():
    """Kivy 默认字体 Roboto 不含汉字，会全部显示成方框。

    把打包进 APK 的 Noto Sans SC（覆盖全部常用简体汉字）注册为默认字体，
    注册名用 'Roboto' 即可让所有控件全局生效。
    """
    from kivy.core.text import LabelBase

    candidates = [FONT_FILE]
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, FONT_FILE))
    except Exception:
        pass
    for path in candidates:
        try:
            if os.path.exists(path):
                LabelBase.register(name='Roboto', fn_regular=path)
                return path
        except Exception:
            traceback.print_exc()
    print('警告: 未找到中文字体 %s，汉字可能显示为方框' % FONT_FILE)
    return None

# 思考强度：界面文字 ↔ DeepSeek API 取值（low / high / max）
EFFORT_VALUE = {'关闭': 'off', '低': 'low', '高': 'high', '最高': 'max'}
EFFORT_LABEL = {v: k for k, v in EFFORT_VALUE.items()}

# 悬浮球点按动作：界面文字 ↔ bridge 内部取值
TAP_VALUE = {'截图搜题': 'screenshot', '最新截图': 'shot', '剪贴板搜题': 'clipboard'}

HELP_TEXT = (
    '【使用步骤】\n'
    '1. 到 platform.deepseek.com 注册并创建 API Key，'
    '在「API 设置」里填入（识图和解题默认都用 deepseek-flash，'
    '也可以在设置里改模型；同一个 Key 即可）。\n\n'
    '2. 点「申请悬浮窗权限」，在系统设置里允许本应用显示在其他应用上层。\n\n'
    '3. 搜题方式（三选一，可在「API 设置」里改悬浮球点按动作）：\n'
    '   【最新截图】推荐，最省事：先按手机截图快捷键把题目截图'
    '（一般是电源键+音量下，或三指下滑），再点悬浮球，'
    'App 会读相册里最新那张截图去搜题。首次使用需允许读取图片。\n'
    '   【截图搜题】点悬浮球自动截屏。点「② 开启截屏授权」后按系统提示'
    '选择要共享的应用；授权成功后悬浮球会自动出现。完全杀掉 App 或锁屏后，'
    '系统会回收授权，需要回来重新点一次「②」。\n'
    '   【剪贴板搜题】长按选中题目文字 → 复制，再点面板里的【读剪贴板】。\n\n'
    '4. 截图授权成功后悬浮球会自动出现；如果选用「最新截图」或'
    '「剪贴板搜题」，也可点「③ 启动悬浮球」并自动退到后台。'
    '然后打开佛脚刷题。\n'
    '   · 点一下悬浮球 = 截图搜题（默认）\n'
    '   · 拖动悬浮球 = 移动位置\n'
    '   · 也可以长按题目文字复制后，在面板里点【读剪贴板】\n\n'
    '5. 答案会显示在悬浮面板里，可拖动、最小化、复制。\n\n'
    '【iQOO/vivo 注意】\n'
    '· 设置 → 电池 → 后台耗电管理 → 允许本应用后台高耗电\n'
    '· 设置 → 应用 → 自启动 → 打开\n'
    '· 不要从最近任务里划掉本应用，否则悬浮球与截屏授权会失效\n\n'
    '本工具仅供个人学习研究，请遵守题目来源平台的规则。'
)

KV = '''
<MainScreen>:
    name: 'main'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(14)
        spacing: dp(8)
        ScrollView:
            size_hint_y: None
            height: dp(150)
            Label:
                id: status
                markup: True
                text: '正在加载状态…'
                size_hint_y: None
                height: max(self.texture_size[1], dp(120))
                text_size: self.width, None
                halign: 'left'
                valign: 'top'
                font_size: sp(14)
        Widget:
            size_hint_y: 0.4
        Button:
            text: '① 申请悬浮窗权限'
            on_press: app.req_overlay()
        Button:
            text: '② 开启截屏授权（重启App后需一次）'
            on_press: app.req_projection()
        Button:
            text: '③ 启动悬浮球，去刷题'
            bold: True
            on_press: app.start_ball()
        Button:
            text: '停止悬浮球'
            on_press: app.stop_ball()
        Button:
            text: 'API 设置'
            on_press: app.sm.current = 'settings'
        Button:
            text: '使用说明'
            on_press: app.sm.current = 'help'
        Button:
            text: '测试 API 连通'
            on_press: app.test_api()

<SettingsScreen>:
    name: 'settings'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(14)
        spacing: dp(6)
        ScrollView:
            do_scroll_x: False
            GridLayout:
                cols: 1
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(6)
                Label:
                    text: 'DeepSeek API 配置'
                    bold: True
                    size_hint_y: None
                    height: dp(30)
                    font_size: sp(16)
                Label:
                    text: 'API Key（platform.deepseek.com → API Keys 创建）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                TextInput:
                    id: key
                    multiline: False
                    write_tab: False
                    password: True
                    size_hint_y: None
                    height: dp(40)
                Label:
                    text: 'API Base URL（默认官方地址，一般不改）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                TextInput:
                    id: base
                    multiline: False
                    write_tab: False
                    size_hint_y: None
                    height: dp(40)
                Label:
                    text: '识图模型（截图识别用；点下面按钮可拉取列表）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                TextInput:
                    id: vmodel
                    multiline: False
                    write_tab: False
                    size_hint_y: None
                    height: dp(40)
                Button:
                    text: '拉取可用模型列表（填入识图模型）'
                    size_hint_y: None
                    height: dp(38)
                    on_press: app.fetch_models('vmodel')
                Label:
                    text: '解题模型（默认 deepseek-flash；也可自己输入）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                TextInput:
                    id: smodel
                    multiline: False
                    write_tab: False
                    size_hint_y: None
                    height: dp(40)
                Button:
                    text: '拉取可用模型列表（填入解题模型）'
                    size_hint_y: None
                    height: dp(38)
                    on_press: app.fetch_models('smodel')
                Label:
                    text: '思考强度（默认「高」，即官方默认 high；「最高」= max）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                Spinner:
                    id: effort
                    text: '高'
                    values: ['关闭', '低', '高', '最高']
                    size_hint_y: None
                    height: dp(40)
                Label:
                    text: '解题提示词（系统提示词，留空则用内置默认）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                TextInput:
                    id: sprompt
                    multiline: True
                    size_hint_y: None
                    height: dp(150)
                    font_size: sp(12)
                Label:
                    text: '识图提示词（截图识别用，留空则用内置默认）'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                TextInput:
                    id: vprompt
                    multiline: True
                    size_hint_y: None
                    height: dp(110)
                    font_size: sp(12)
                Button:
                    text: '恢复默认提示词'
                    size_hint_y: None
                    height: dp(40)
                    on_press: app.reset_prompts()
                Label:
                    text: '悬浮球点按动作'
                    size_hint_y: None
                    height: dp(24)
                    halign: 'left'
                    text_size: self.width, None
                    font_size: sp(13)
                Spinner:
                    id: action
                    text: '截图搜题'
                    values: ['截图搜题', '最新截图', '剪贴板搜题']
                    size_hint_y: None
                    height: dp(40)
        Widget:
            size_hint_y: 0.3
        Button:
            text: '保存并返回'
            bold: True
            on_press: app.save_settings()
        Button:
            text: '返回（不保存）'
            on_press: app.sm.current = 'main'

<HelpScreen>:
    name: 'help'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(14)
        spacing: dp(8)
        ScrollView:
            Label:
                text: root.help_text
                size_hint_y: None
                height: self.texture_size[1]
                text_size: self.width, None
                halign: 'left'
                valign: 'top'
                font_size: sp(14)
        Button:
            text: '返回'
            size_hint_y: None
            height: dp(44)
            on_press: app.sm.current = 'main'

<DesktopScreen>:
    name: 'desktop'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(14)
        spacing: dp(8)
        Label:
            text: '桌面调试模式（Android 端为悬浮窗搜题）'
            bold: True
            size_hint_y: None
            height: dp(30)
        TextInput:
            id: dkey
            hint_text: 'DeepSeek API Key'
            multiline: False
            write_tab: False
            password: True
            size_hint_y: None
            height: dp(40)
        TextInput:
            id: dquestion
            hint_text: '输入题目文字…'
            multiline: True
            size_hint_y: None
            height: dp(90)
        TextInput:
            id: dimage
            hint_text: '或填本地截图路径（jpg/png），优先使用'
            multiline: False
            write_tab: False
            size_hint_y: None
            height: dp(40)
        Button:
            text: '提问'
            size_hint_y: None
            height: dp(44)
            on_press: app.desktop_ask()
        ScrollView:
            Label:
                id: dresult
                text: '结果会显示在这里'
                size_hint_y: None
                height: max(self.texture_size[1], dp(80))
                text_size: self.width, None
                halign: 'left'
                valign: 'top'
                font_size: sp(13)

ScreenManager:
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

    def build(self):
        # 必须赶在创建任何控件之前注册中文字体
        _register_cjk_font()
        self.store = JsonStore(os.path.join(self.user_data_dir, 'settings.json'))
        self.sm = Builder.load_string(KV)
        self.load_settings()
        if not IS_ANDROID:
            self.sm.current = 'desktop'
        # 接管安卓返回键，否则按返回会直接把应用切到后台
        Window.bind(on_keyboard=self._on_key)
        Clock.schedule_interval(self.refresh_status, 2)
        return self.sm

    def _on_key(self, window, key, *largs):
        """拦截安卓返回键（Python 侧表现为 key == 27）。

        Kivy 的默认处理是 mActivity.moveTaskToBack(True)，也就是直接切到
        后台——用户看到的就是"点进去再按返回，程序退出了"。
        这里改成：先关弹窗 → 再回主界面 → 只有本来就在主界面才切后台。
        桌面端不干预。
        """
        if not IS_ANDROID or key != 27:
            return False
        try:
            for child in list(Window.children):
                if isinstance(child, Popup):
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
        """从后台回到 App 时强制重绘。

        实测（MuMu/Android 15）从后台回来会黑屏：SDL 日志显示绘图表面
        已重建、nativeResume 已调用，但 Kivy 没有重绘。这里主动请求刷新，
        并延后两次以防首帧过早。
        """
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
            'vision_model': s.ids.vmodel.text.strip() or ai_core.cfg['vision_model'],
            'solve_model': s.ids.smodel.text.strip() or ai_core.cfg['solve_model'],
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
    def refresh_status(self, dt):
        if self.sm.current not in ('main',):
            return
        lines = ['[b]运行状态[/b]']
        if IS_ANDROID and bridge:
            lines.append('悬浮窗权限：%s' % (
                '[color=00c853]√ 已授权[/color]' if bridge.has_overlay_permission()
                else '[color=ff5252]× 未授权[/color]'))
            lines.append('截屏授权：%s' % (
                '[color=00c853]√ 已持有[/color]' if bridge.has_projection()
                else '[color=ffb300]○ 未授权[/color]'))
        else:
            lines.append('当前：桌面调试模式')
        lines.append('API Key：%s' % (
            '[color=00c853]√ 已设置[/color]' if ai_core.cfg['api_key']
            else '[color=ff5252]× 未设置[/color]'))
        lines.append('模型：%s（识图）/ %s（解题）' % (
            ai_core.cfg['vision_model'], ai_core.cfg['solve_model']))
        try:
            self.sm.get_screen('main').ids.status.text = '\n'.join(lines)
        except Exception:
            pass

    # ---------- 按钮动作 ----------
    def req_overlay(self):
        if not (IS_ANDROID and bridge):
            self.popup('提示', '仅 Android 端支持')
            return
        bridge.request_overlay_permission()

    def req_projection(self):
        if not (IS_ANDROID and bridge):
            self.popup('提示', '仅 Android 端支持')
            return
        bridge.request_projection()

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
        # 必须留住这个弹窗的引用才能关掉它：之前直接丢弃返回值，
        # 结果「测试中…」永远留在屏幕上，按返回键关掉结果弹窗后
        # 底下露出来的就是它，看起来像卡在"正在请求接口"。
        busy = self.popup('测试中…', '正在请求 DeepSeek 接口…')

        def done(msg):
            try:
                busy.dismiss()
            except Exception:
                pass
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
        p = Popup(title=title, content=Label(text=msg, font_size=sp(13)),
                  size_hint=(0.85, 0.5))
        p.open()
        return p

    # ---------- 模型列表 ----------
    def fetch_models(self, target):
        """从接口拉取可用模型，弹出列表供点选（失败时提示手动输入）。"""
        busy = self.popup('获取中…', '正在从 DeepSeek 拉取模型列表…')

        def done(ids=None, err=None):
            try:
                busy.dismiss()
            except Exception:
                pass
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
        """把拉取到的模型列成一个可点的列表，点一下填进对应输入框。"""
        s = self.sm.get_screen('settings')
        which = '识图模型' if target == 'vmodel' else '解题模型'
        box = BoxLayout(orientation='vertical', spacing=dp(6),
                        padding=dp(8))
        grid = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        pop = Popup(title='点选填入%s' % which, content=box,
                    size_hint=(0.9, 0.6))

        def make_setter(model_name):
            def _set(*args):
                s.ids[target].text = model_name
                pop.dismiss()
            return _set

        for mid in ids:
            b = Button(text=mid, size_hint_y=None, height=dp(46))
            b.bind(on_release=make_setter(mid))
            grid.add_widget(b)
        sv = ScrollView()
        sv.add_widget(grid)
        box.add_widget(sv)
        pop.open()


if __name__ == '__main__':
    FojiaoApp().run()

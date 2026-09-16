# -*- coding: utf-8 -*-
"""DeepSeek API 客户端。

两条链路：
  1. 截图识题：deepseek-flash 视觉模型，直接传截图(base64)提取题目文字
  2. 解题：默认也是 deepseek-flash（可在 App 的「API 设置」里换成任意可用模型）

DeepSeek 为 OpenAI 兼容接口，官方文档：
  https://api-docs.deepseek.com/zh-cn/guides/vision/
仅依赖标准库（urllib），便于 buildozer 打包。
"""
import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request

# Android 上的 Python 找不到系统 CA 证书，HTTPS 会直接报
# "certificate verify failed"。把 certifi 自带的证书包通过环境变量
# 指给 OpenSSL（必须在创建 SSL 上下文之前设置）。
try:
    import certifi

    _CA_BUNDLE = certifi.where()
    os.environ.setdefault('SSL_CERT_FILE', _CA_BUNDLE)
    os.environ.setdefault('REQUESTS_CA_BUNDLE', _CA_BUNDLE)
except Exception:
    _CA_BUNDLE = None


def _ssl_context():
    """HTTPS 请求用的 SSL 上下文。

    Android 上 p4a 打包的 OpenSSL **不认 SSL_CERT_FILE**：实测
    create_default_context() 出来的信任库是空的（cert_store_stats 全 0），
    校验就直接死在 "self signed certificate in certificate chain"。
    光设环境变量没用，必须显式把 certifi 的证书包加载进上下文
    （加载后是 121 把 CA）。
    """
    ctx = ssl.create_default_context()
    if _CA_BUNDLE:
        try:
            ctx.load_verify_locations(cafile=_CA_BUNDLE)
        except Exception:
            pass
    return ctx

cfg = {
    'api_key': '',
    'base_url': 'https://api.deepseek.com',
    'vision_model': 'deepseek-flash',   # 截图 OCR/识题（视觉模型）
    'solve_model': 'deepseek-flash',     # 解题（默认；可在设置里换）
    # 思考强度：off / low / high / max（官方默认就是 high）
    'think_effort': 'high',
    'ocr_prompt': '',                   # 空=用下面的默认值
    'solve_prompt': '',
}

#: 识图提示词：纯识别，不推理，追求又快又准
DEFAULT_OCR_PROMPT = (
    '你是精确的OCR引擎。请逐字提取图片中的题目及其选项（A/B/C/D等），'
    '保持原有格式与编号输出。不要解答、不要翻译、不要添加任何解释。'
    '如果图中没有题目文字，只输出：__NO_QUESTION__'
)

#: 解题提示词（系统提示词）：面向医学类题目，并约束输出格式
DEFAULT_SOLVE_PROMPT = (
    '你是一名专业、严谨的医学考试解题助手，帮用户解答医学类练习题'
    '（基础医学、临床各科、护理、药学、检验等；题型包括单选、多选、'
    '判断、名词解释、简答和病例分析）。回答请严格遵循以下格式：\n'
    '【答案】先直接给出答案。单选题给字母+内容；多选题列出全部正确选项；'
    '判断题写“对/错”；简答与病例题给出要点式结论。\n'
    '【解题思路】分步骤说明依据，点出关键的鉴别点或易混淆之处，'
    '尽量依据公认教材结论或临床指南要点。\n'
    '多选题要逐个选项判断“对/错 + 一句理由”。\n'
    '病例题按“诊断 → 诊断依据 → 鉴别诊断 → 处理原则”组织。\n'
    '若题目信息不足或有歧义，先指出问题，再按最可能的理解作答；'
    '若涉及争议内容或超出常见教材范围，请明确说明。\n'
    '注意：答案会显示在手机的小窗口里，请用纯文本，不要使用 LaTeX'
    '（美元符号包裹、反斜杠 frac 之类）或 Markdown 标记（星号、井号、'
    '反引号），也不要用上下标小字符（写成 v0、x^5 这种即可）。'
)

# 兼容旧名字
OCR_PROMPT = DEFAULT_OCR_PROMPT
SYSTEM_PROMPT = DEFAULT_SOLVE_PROMPT


#: 字体实测缺失的字符 → 能显示的替代写法（见 _fontcheck 结论）
_FONT_UNSAFE = {
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
    '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
    '₋': '-', '₊': '+',
    '⁰': '^0', '⁵': '^5', '⁶': '^6', '⁷': '^7', '⁸': '^8', '⁹': '^9',
    '⁻': '^-', '⁺': '^+', 'ⁿ': '^n',
    '✔': '√', '✓': '√', '✘': '×', '✗': '×', '❌': '×',
    '⚠': '注意', '️': '',          # U+FE0F 变体选择符
}

#: 字体已确认支持的 Unicode 上标，保留不动
_FONT_SAFE_SUP = set('¹²³⁴')


def font_safe(text):
    """把字体里没有的字符换成可显示的写法。

    Noto Sans SC 实测缺下标（₀-₉）以及 ⁰⁵⁶⁷⁸⁹⁻⁺ⁿ 等，
    而模型输出里很常见（如 v₀），不替换就会显示成方框。
    """
    for a, b in _FONT_UNSAFE.items():
        if a in text:
            text = text.replace(a, b)
    return text


def solve_prompt():
    """当前生效的解题提示词（用户自定义优先）。"""
    return (cfg.get('solve_prompt') or '').strip() or DEFAULT_SOLVE_PROMPT


def ocr_prompt():
    """当前生效的识图提示词。"""
    return (cfg.get('ocr_prompt') or '').strip() or DEFAULT_OCR_PROMPT

# LaTeX 常见符号 → Unicode，保证手机上直接可读
_LATEX_SYMBOLS = {
    '\\Delta': 'Δ', '\\delta': 'δ', '\\alpha': 'α', '\\beta': 'β',
    '\\gamma': 'γ', '\\theta': 'θ', '\\lambda': 'λ', '\\mu': 'μ',
    '\\pi': 'π', '\\rho': 'ρ', '\\sigma': 'σ', '\\phi': 'φ',
    '\\omega': 'ω', '\\Omega': 'Ω', '\\varepsilon': 'ε',
    '\\times': '×', '\\div': '÷', '\\cdot': '·', '\\pm': '±',
    '\\leq': '≤', '\\le': '≤', '\\geq': '≥', '\\ge': '≥',
    '\\neq': '≠', '\\ne': '≠', '\\approx': '≈', '\\equiv': '≡',
    '\\propto': '∝', '\\infty': '∞', '\\angle': '∠', '\\degree': '°',
    '\\perp': '⊥', '\\parallel': '∥', '\\sum': 'Σ', '\\int': '∫',
    '\\in': '∈', '\\notin': '∉', '\\subset': '⊂', '\\cup': '∪',
    '\\cap': '∩', '\\forall': '∀', '\\exists': '∃',
    '\\rightarrow': '→', '\\to': '→', '\\Rightarrow': '⇒',
    '\\leftarrow': '←', '\\Leftarrow': '⇐', '\\leftrightarrow': '↔',
    '\\ldots': '…', '\\cdots': '…', '\\dots': '…',
}


def _strip_latex(text):
    """把 LaTeX 尽量转成手机上好读的纯文本。"""
    t = text
    # 去公式定界符
    t = t.replace('$$', '').replace('\\[', '').replace('\\]', '')
    t = t.replace('\\(', '').replace('\\)', '').replace('$', '')
    t = re.sub(r'\\(?:left|right|,|;|!|:|quad|qquad|displaystyle)\s*', '', t)
    t = re.sub(r'\\\s+', ' ', t)          # LaTeX 的 "\ "（反斜杠+空格）
    # 花括号形式的结构（嵌套有限，重复几轮足够）
    for _ in range(4):
        t = re.sub(r'\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'(\1)/(\2)', t)
        t = re.sub(r'\\sqrt\s*\[[^\]]*\]\s*\{([^{}]*)\}', r'√(\1)', t)
        t = re.sub(r'\\sqrt\s*\{([^{}]*)\}', r'√(\1)', t)
        t = re.sub(r'\\text\s*\{([^{}]*)\}', r'\1', t)
        t = re.sub(r'\\mathrm\s*\{([^{}]*)\}', r'\1', t)
    # 希腊字母/运算符
    for a, b in _LATEX_SYMBOLS.items():
        t = t.replace(a, b)
    # 字体实测：只有上标 ¹²³⁴ 存在；下标和 ⁰⁵⁶⁷⁸⁹⁻⁺ⁿ 都没有，
    # 所以下标一律写成普通数字（v_0 -> v0），上标只对 1~4 用 Unicode
    sups = {'1': '¹', '2': '²', '3': '³', '4': '⁴'}

    def _sup(m):
        body = m.group(1)
        if all(c in sups for c in body):
            return ''.join(sups[c] for c in body)   # x^{2} -> x²
        return '^' + body                           # y^{-1} -> y^-1

    t = re.sub(r'\^\s*\{([^{}]*)\}', _sup, t)
    t = re.sub(r'_\s*\{([^{}]*)\}', lambda m: m.group(1), t)
    t = re.sub(r'\^([0-9n])',
               lambda m: sups.get(m.group(1), '^' + m.group(1)), t)
    t = re.sub(r'_([0-9])', r'\1', t)
    # 剩下的反斜杠命令：保留命令名，去掉反斜杠
    t = re.sub(r'\\([a-zA-Z]+)', r'\1', t)
    # 收紧数学符号后面多余的空格：(Δ v)/(Δ t) -> (Δv)/(Δt)
    t = re.sub(r'([ΔδθαβγλμπρσφωΩ×÷·±≤≥≠≈≡√∝∞∠°⊥∥])[ \t]+', r'\1', t)
    return t


def _post_chat(model, messages, timeout=180, think=None):
    """调用对话接口。

    think=None 跟随设置里的思考强度；think=False 强制关闭思考（识图用，
    纯识别不需要推理，关掉更快）。
    """
    if not cfg['api_key']:
        raise RuntimeError('未配置 DeepSeek API Key，请打开 App 的「API 设置」填写')
    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    payload = {'model': model, 'messages': messages, 'stream': False}

    effort = (cfg.get('think_effort') or 'high').lower()
    if think is False or effort == 'off':
        # 关闭思考模式
        payload['thinking'] = {'type': 'disabled'}
        payload['temperature'] = 0.2      # 只有非思考模式 temperature 才生效
    else:
        payload['thinking'] = {'type': 'enabled'}
        # 官方取值 low / high / max（medium、xhigh 会被静默映射为 high）
        payload['reasoning_effort'] = effort if effort in (
            'low', 'high', 'max') else 'high'

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + cfg['api_key'],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        raise RuntimeError('DeepSeek 接口错误 HTTP %d: %s' % (e.code, detail))
    except Exception as e:
        raise RuntimeError('网络错误：%s' % e)
    try:
        msg = data['choices'][0]['message']
    except Exception:
        raise RuntimeError('接口返回异常: ' + json.dumps(data, ensure_ascii=False)[:300])
    return (msg.get('content') or ''), (msg.get('reasoning_content') or '')


def ocr_question(image_path):
    """把本地截图发给视觉模型，返回识别出的题目文本。

    纯识别任务，强制关闭思考模式以求速度（思考强度设置只作用于解题）。
    """
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    messages = [{
        'role': 'user',
        'content': [
            {'type': 'text', 'text': ocr_prompt()},
            {'type': 'image_url',
             'image_url': {'url': 'data:image/jpeg;base64,' + b64}},
        ],
    }]
    text, _ = _post_chat(cfg['vision_model'], messages, timeout=120, think=False)
    text = text.strip()
    if '__NO_QUESTION__' in text or len(text) < 8:
        raise RuntimeError('截图中未识别到题目文字，可重试一次，或在面板中手动输入题目')
    return text


def solve_question(question):
    """调用解题模型，返回答案+解题思路的文本。

    使用「API 设置」里的自定义提示词（为空则用内置默认），
    并按思考强度设置决定是否开启思考模式。
    """
    messages = [
        {'role': 'system', 'content': solve_prompt()},
        {'role': 'user', 'content': question},
    ]
    content, reasoning = _post_chat(cfg['solve_model'], messages, timeout=300)
    if not content:
        content = reasoning
    if not content:
        raise RuntimeError('模型没有返回内容，请重试')
    return content


def list_models():
    """从接口拉取可用模型列表（GET /models），供界面点选。

    DeepSeek 目前通常只返回 deepseek-flash 与 deepseek-v4-pro；
    旧名字（deepseek-chat 等）仍可调用但不再列出，所以界面允许手动输入。
    """
    if not cfg['api_key']:
        raise RuntimeError('未配置 DeepSeek API Key，请先填写')
    url = cfg['base_url'].rstrip('/') + '/models'
    req = urllib.request.Request(
        url, headers={'Authorization': 'Bearer ' + cfg['api_key']})
    try:
        with urllib.request.urlopen(req, timeout=30,
                                    context=_ssl_context()) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        raise RuntimeError('获取模型列表失败 HTTP %d: %s' % (e.code, detail))
    except Exception as e:
        raise RuntimeError('网络错误：%s' % e)
    ids = [m.get('id') for m in (data.get('data') or []) if m.get('id')]
    if not ids:
        raise RuntimeError('接口没有返回任何模型，可手动填写模型名')
    return ids


def test_connection():
    c, _ = _post_chat('deepseek-chat',
                      [{'role': 'user', 'content': '请只回复四个字：连接成功'}],
                      timeout=30)
    return c.strip() or '(空响应)'


def plain_text(md):
    """轻量清理 Markdown 与 LaTeX，便于 Android TextView 显示。"""
    t = md.replace('\r\n', '\n')
    t = re.sub(r'```[a-zA-Z0-9]*\n?', '', t)
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.M)
    t = t.replace('**', '').replace('__', '')
    t = _strip_latex(t)
    t = font_safe(t)
    # 压缩多余空行
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()

# -*- coding: utf-8 -*-
"""DeepSeek API 客户端。

两条链路：
  1. 截图识题：deepseek-flash 视觉模型，直接传截图(base64)提取题目文字
  2. 解题：deepseek-chat / deepseek-reasoner 生成答案与解题思路

DeepSeek 为 OpenAI 兼容接口，官方文档：
  https://api-docs.deepseek.com/zh-cn/guides/vision/
仅依赖标准库（urllib），便于 buildozer 打包。
"""
import base64
import json
import os
import re
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
    pass

cfg = {
    'api_key': '',
    'base_url': 'https://api.deepseek.com',
    'vision_model': 'deepseek-flash',   # 截图 OCR/识题
    'solve_model': 'deepseek-chat',     # 解题（可选 deepseek-reasoner 思路更细但更慢）
}

OCR_PROMPT = (
    '你是精确的OCR引擎。请逐字提取图片中的题目及其选项（A/B/C/D等），'
    '保持原有格式与编号输出。不要解答、不要翻译、不要添加任何解释。'
    '如果图中没有题目文字，只输出：__NO_QUESTION__'
)

SYSTEM_PROMPT = (
    '你是一名专业、严谨的中文解题助手，帮用户解答来自学习类App的练习题'
    '（多为单选/多选/判断题，也可能是简答）。回答请遵循以下格式：\n'
    '【答案】直接给出答案（选择题给字母+内容）\n'
    '【解题思路】分步骤讲清为什么，简洁清晰，必要时给出关键依据或公式。\n'
    '如果题目不完整或有歧义，先指出问题所在，再按最可能的含义作答。'
)


def _post_chat(model, messages, timeout=180):
    if not cfg['api_key']:
        raise RuntimeError('未配置 DeepSeek API Key，请打开 App 的「API 设置」填写')
    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    payload = {'model': model, 'messages': messages, 'stream': False}
    if 'reasoner' not in model:  # deepseek-reasoner 不支持自定义 temperature
        payload['temperature'] = 0.2
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + cfg['api_key'],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
    """把本地截图发给 deepseek-flash，返回识别出的题目文本。"""
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    messages = [{
        'role': 'user',
        'content': [
            {'type': 'text', 'text': OCR_PROMPT},
            {'type': 'image_url',
             'image_url': {'url': 'data:image/jpeg;base64,' + b64}},
        ],
    }]
    text, _ = _post_chat(cfg['vision_model'], messages, timeout=120)
    text = text.strip()
    if '__NO_QUESTION__' in text or len(text) < 8:
        raise RuntimeError('截图中未识别到题目文字，可重试一次，或在面板中手动输入题目')
    return text


def solve_question(question):
    """调用解题模型，返回答案+解题思路的 Markdown 文本。"""
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': question},
    ]
    content, reasoning = _post_chat(cfg['solve_model'], messages, timeout=300)
    if not content:
        content = reasoning
    if not content:
        raise RuntimeError('模型没有返回内容，请重试')
    return content


def test_connection():
    c, _ = _post_chat('deepseek-chat',
                      [{'role': 'user', 'content': '请只回复四个字：连接成功'}],
                      timeout=30)
    return c.strip() or '(空响应)'


def plain_text(md):
    """轻量清理 Markdown，便于 Android TextView 显示。"""
    t = md.replace('\r\n', '\n')
    t = re.sub(r'```[a-zA-Z0-9]*\n?', '', t)
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.M)
    t = t.replace('**', '')
    return t.strip()

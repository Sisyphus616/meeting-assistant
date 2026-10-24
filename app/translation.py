"""Dedicated Hy-MT translation. Never falls back to the summary language model."""
import re
import subprocess
import time

import httpx


def translation_prompt(text, target, context='', glossary=''):
    language = {'en': '英语', 'zh': '中文'}[target]
    parts = []
    if context:
        parts.append(f'背景信息（仅供理解，不要翻译）：\n{context[-1200:]}')
    if glossary:
        parts.append(f'术语参考（保留专有名词与缩写）：{glossary[:1000]}')
    parts.append(f'将以下文本翻译为{language}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}')
    return '\n\n'.join(parts)


class HyMTTranslator:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.logfile = None
        self.model = self.tokenizer = None

    def _start_server(self):
        if self.process is not None:
            if self.process.poll() is not None:
                raise RuntimeError('翻译服务已退出，请查看 data/translation-server.log 并重启应用。')
            return
        s = self.settings
        if not s.translation_gguf.is_file() or not s.translation_binary.is_file():
            raise RuntimeError('缺少翻译模型或 llama.cpp。请先运行 scripts/setup_translation.py。')
        s.data_dir.mkdir(parents=True, exist_ok=True)
        self.logfile = (s.data_dir / 'translation-server.log').open('a')
        self.process = subprocess.Popen([
            str(s.translation_binary), '-m', str(s.translation_gguf),
            '--host', '127.0.0.1', '--port', str(s.translation_port),
            '-c', '4096', '-t', str(s.threads), '-ngl', '0', '-np', '1',
            '--alias', s.translation_model, '--jinja', '--no-warmup',
        ], stdout=self.logfile, stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{s.translation_port}'
        # Verify the child remains alive even if another service answers this port.
        for _ in range(240):
            if self.process.poll() is not None:
                raise RuntimeError('翻译服务启动失败，请查看 data/translation-server.log（可能端口已占用或模型不兼容）。')
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    response = client.get(base + '/health')
                if response.status_code == 200:
                    time.sleep(.1)
                    if self.process.poll() is None:
                        return
            except httpx.HTTPError:
                pass
            time.sleep(.25)
        self.close()
        raise RuntimeError('翻译模型加载超时，请检查翻译服务日志。')

    def _http(self, prompt):
        s = self.settings
        if s.translation_backend == 'llama_cpp':
            self._start_server()
            base = f'http://127.0.0.1:{s.translation_port}/v1'
        else:
            base = s.translation_base_url
        headers = {'Authorization': f'Bearer {s.translation_api_key}'} if s.translation_api_key else {}
        with httpx.Client(timeout=180, trust_env=False) as client:
            response = client.post(base + '/chat/completions', headers=headers, json={
                'model': s.translation_model, 'messages': [{'role':'user', 'content':prompt}],
                'temperature': 0, 'top_p': .6, 'top_k': 20, 'repeat_penalty': 1.05,
                'max_tokens': 768, 'stream': False,
            })
        response.raise_for_status()
        choice = response.json()['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise RuntimeError('译文超过输出长度限制，请缩短单次输入。')
        return choice['message']['content']

    def _transformers(self, prompt):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        s = self.settings
        device = ('cuda' if torch.cuda.is_available() else 'cpu') if s.device == 'auto' else s.device
        dtype = getattr(torch, s.dtype) if s.dtype != 'auto' else (torch.float16 if device.startswith('cuda') else torch.float32)
        if self.model is None:
            self.tokenizer = AutoTokenizer.from_pretrained(s.translation_model, local_files_only=s.local_only)
            self.model = AutoModelForCausalLM.from_pretrained(s.translation_model, dtype=dtype,
                attn_implementation='sdpa', local_files_only=s.local_only).to(device).eval()
        prompt = self.tokenizer.apply_chat_template([{'role':'user','content':prompt}], tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(prompt, return_tensors='pt').to(device)
        if inputs['input_ids'].shape[1] > 3500:
            raise RuntimeError('原文及上下文过长，请缩短单次输入。')
        with torch.inference_mode():
            outputs = self.model.generate(**inputs, max_new_tokens=768, do_sample=False, repetition_penalty=1.05)
        generated = outputs[0,inputs['input_ids'].shape[1]:]
        if len(generated) >= 768:
            raise RuntimeError('译文超过输出长度限制，请缩短单次输入。')
        return self.tokenizer.decode(generated, skip_special_tokens=True)

    def translate(self, text, target, context, glossary):
        prompt = translation_prompt(text, target, context, glossary)
        if self.settings.translation_backend in ('llama_cpp','compatible'):
            result = self._http(prompt)
        elif self.settings.translation_backend == 'transformers':
            result = self._transformers(prompt)
        else:
            raise RuntimeError('TRANSLATION_BACKEND 必须为 llama_cpp、compatible 或 transformers。')
        result = re.sub(r'<think>.*?</think>', '', result or '', flags=re.S).strip()
        if not result or '<think>' in result:
            raise RuntimeError('翻译模型未返回完整译文。')
        return result

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.logfile:
            self.logfile.close()
            self.logfile = None

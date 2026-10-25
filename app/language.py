"""Local Chinese/English routing; Chinese source text is kept unchanged."""
import re

HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]')
WORDS = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*(?:'[A-Za-z]+)?")


def is_chinese_text(text):
    han = len(HAN.findall(text))
    if not han:
        return False
    # Acronyms / versioned model names do not make a Chinese sentence English.
    words = [word for word in WORDS.findall(text)
             if not word.isupper() and not any(c.isdigit() for c in word)]
    return han >= len(words)


def recognition_source(meeting):
    # English -> Chinese is a preferred direction, not a command to turn spoken
    # Chinese into English. Let ASR identify occasional Chinese speech as well.
    if meeting['source'] == 'en' and meeting['target'] == 'zh':
        return 'auto'
    return meeting['source']

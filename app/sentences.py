"""Conservative sentence boundaries for confirmed ASR, without inventing text."""
import re

ABBREVIATIONS = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'e.g', 'i.e'}
CLOSING = '\"\'”’」』）)]'


def split_sentences(text):
    text = text.strip()
    if not text:
        return []
    result, start, i = [], 0, 0
    while i < len(text):
        char = text[i]
        boundary = char in '。！？!?\n'
        if char == '.':
            before, after = text[i - 1:i], text[i + 1:i + 2]
            token = re.search(r'([A-Za-z.]+)$', text[start:i])
            word = token.group(1).lower() if token else ''
            boundary = (not after or after.isspace() or after in CLOSING or '\u4e00' <= after <= '\u9fff')
            if before.isdigit() and after.isdigit():
                boundary = False
            if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                boundary = False
            if re.fullmatch(r'(?:[a-z]\.)+[a-z]', word):
                boundary = False
        if boundary:
            end = i + 1
            while end < len(text) and text[end] in '。！？!?….' + CLOSING:
                end += 1
            sentence = text[start:end].strip()
            if sentence:
                result.append(sentence)
            start, i = end, end
        else:
            i += 1
    if text[start:].strip():
        result.append(text[start:].strip())
    return result

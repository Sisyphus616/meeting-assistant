"""Deterministic corpus error counts and descriptive statistics for experiments."""
import math
import re
import unicodedata


def units(text, language):
    text = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    # AMI disfluency spellings are retained; only annotation markup is removed.
    text = re.sub(r'<[^>]*>|\[[^\]]*\]|\{[^}]*\}', ' ', text)
    if language == 'zh':
        return [c for c in text if c.isalnum()]
    # TED/AMI references tokenize contractions: "i 'm", "do n't".
    text = re.sub(r"\b([a-z]+)\s+(n't|'(?:s|re|ve|m|ll|d))\b", r'\1\2', text)
    # Keep non-Latin hallucinations as error tokens, rather than dropping them.
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text)


def error_counts(reference, hypothesis, language='en'):
    ref, hyp = units(reference, language), units(hypothesis, language)
    if not ref:
        raise ValueError('Reference has no scoring units')
    # Tuples contain (edit distance, substitutions, deletions, insertions).
    previous = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i, expected in enumerate(ref, 1):
        current = [(i, 0, i, 0)]
        for j, actual in enumerate(hyp, 1):
            if expected == actual:
                current.append(previous[j - 1])
                continue
            distance, sub, delete, insert = previous[j - 1]
            choices = [(distance + 1, sub + 1, delete, insert)]
            distance, sub, delete, insert = previous[j]
            choices.append((distance + 1, sub, delete + 1, insert))
            distance, sub, delete, insert = current[j - 1]
            choices.append((distance + 1, sub, delete, insert + 1))
            current.append(min(choices, key=lambda value: value[0]))
        previous = current
    edits, substitutions, deletions, insertions = previous[-1]
    return dict(metric='CER' if language == 'zh' else 'WER',
                reference_units=len(ref), hypothesis_units=len(hyp),
                substitutions=substitutions, deletions=deletions,
                insertions=insertions, errors=edits, rate=edits / len(ref))


def corpus_error(rows):
    if not rows:
        return None
    count = sum(row['reference_units'] for row in rows)
    result = {key: sum(row[key] for row in rows) for key in
              ('reference_units', 'hypothesis_units', 'substitutions', 'deletions', 'insertions', 'errors')}
    result['rate'] = result['errors'] / count
    return result


def describe(values):
    values = sorted(value for value in values if value is not None and math.isfinite(value))
    if not values:
        return {'n': 0}

    def quantile(q):
        position = (len(values) - 1) * q
        lo, hi = math.floor(position), math.ceil(position)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    return dict(n=len(values), mean=sum(values) / len(values), median=quantile(.5),
                p95=quantile(.95), min=values[0], max=values[-1])


def validate_rating(rating):
    keys = ('faithfulness', 'terminology', 'fluency')
    for key in keys:
        value = rating[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 5:
            raise ValueError(f'Invalid judge score: {key}')
        if value * 2 != round(value * 2):
            raise ValueError(f'Judge score must use half-point increments: {key}')
    if not isinstance(rating.get('evidence'), str) or not rating['evidence'].strip():
        raise ValueError('Judge must supply evidence')
    return round(sum(rating[key] * weight for key, weight in
                     zip(keys, (.6, .2, .2))), 4)


def audit_trial(row):
    """Derive delivery status and target text from stored segments, without editing raw logs.

    Chinese segments are already target-language output. A bilingual ASR result
    can contain Chinese filler words even for an English source recording.
    """
    result = dict(row)
    result['quality'] = error_counts(row['reference'], row['transcript'], row['language'])
    result['quality_recomputed'] = result['quality'] != row['quality']
    segments = row['segments']
    result['translation'] = '\n'.join(s['text'] if s['translation_skipped'] else s['translation']
                                      for s in segments if s['translation_skipped'] or s['translation'])
    generated = {'Missing English-to-Chinese translation', 'No confirmed transcript'}
    result['errors'] = [e for e in row['errors'] if e not in generated]
    if not row['transcript'].strip():
        result['errors'].append('No confirmed transcript')
    if row['language'] == 'en' and (not result['translation'].strip() or any(
            not s['translation'].strip() and not s['translation_skipped'] for s in segments)):
        result['errors'].append('Missing English-to-Chinese translation')
    result['success'] = not result['errors']
    result['collector_status_recomputed'] = row['success'] != result['success']
    return result

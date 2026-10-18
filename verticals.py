"""
verticals.py — per-profession ("vertical") configuration, shared by both
backends and the student portal. The browser has the same lookup logic in
vertical.js; both read the JSON files in verticals/.

A vertical file (verticals/<id>.json) holds:
  id, icon, label {f, m}, description,
  terms     — UI words for this profession: "client", "appointment", ...
              A term is a string, or {f, m} when it depends on the business
              owner's gender. Strings may contain {placeholders}.
  features  — on/off flags for profession-specific features (exams, ...)
  defaults  — defaults for new records (price, durationMinutes)
  fields    — default custom fields offered to a new business

All profession-specific behaviour must come from these files — code asks
has(vertical, 'exams') / t(vertical, 'client'), never "if profession == ...".
"""
import json
import os

VERTICALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verticals')
DEFAULT_VERTICAL = 'tutor'
GENDERS = ('f', 'm')
DEFAULT_GENDER = 'f'
REQUIRED_KEYS = ('id', 'label', 'terms', 'features', 'defaults', 'fields')

_cache = {}


def _read(vid):
    with open(os.path.join(VERTICALS_DIR, f'{vid}.json'), encoding='utf-8') as f:
        data = json.load(f)
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f'vertical {vid!r} is missing {missing}')
    if data['id'] != vid:
        raise ValueError(f'vertical file {vid}.json has id {data["id"]!r}')
    return data


def available():
    """Ids of every vertical file (files starting with "_" are shared data, not verticals)."""
    return sorted(fn[:-5] for fn in os.listdir(VERTICALS_DIR)
                  if fn.endswith('.json') and not fn.startswith('_'))


def normalize(vid):
    return vid if vid in available() else DEFAULT_VERTICAL


def normalize_gender(g):
    return g if g in GENDERS else DEFAULT_GENDER


def load(vid):
    """The vertical's config; unknown ids fall back to the default vertical.
    Terms missing from a non-default vertical inherit the default's terms."""
    vid = normalize(vid)
    if vid not in _cache:
        data = _read(vid)
        if vid != DEFAULT_VERTICAL:
            base = _read(DEFAULT_VERTICAL)
            data['terms'] = {**base['terms'], **data['terms']}
            data['features'] = {**{k: False for k in base['features']}, **data['features']}
        _cache[vid] = data
    return _cache[vid]


def brand():
    with open(os.path.join(VERTICALS_DIR, '_brand.json'), encoding='utf-8') as f:
        return json.load(f)


def t(vid, key, gender=DEFAULT_GENDER, **params):
    """Look up a term for a vertical. Gendered terms pick the owner's form;
    {placeholders} are filled from params. Unknown keys return the key itself
    so a missing term is visible instead of crashing."""
    val = load(vid)['terms'].get(key, key)
    if isinstance(val, dict):
        val = val.get(normalize_gender(gender)) or val.get(DEFAULT_GENDER) or next(iter(val.values()))
    for k, v in params.items():
        val = val.replace('{' + k + '}', str(v))
    return val


def has(vid, feature):
    return bool(load(vid)['features'].get(feature, False))


def summary():
    """Public list for the signup picker."""
    ids = sorted(available(), key=lambda v: (load(v).get('order', 99), v))
    return [{'id': v, 'icon': load(v).get('icon', ''), 'label': load(v)['label'],
             'description': load(v).get('description', '')} for v in ids]

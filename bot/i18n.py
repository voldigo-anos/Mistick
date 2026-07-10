import logging
import os

import yaml

logger = logging.getLogger(__name__)

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'locales')
DEFAULT_LOCALE = 'en'

_cache = {}


def _load_locale(locale: str) -> dict:
    if locale in _cache:
        return _cache[locale]

    path = os.path.join(LOCALES_DIR, '{}.yaml'.format(locale))
    if not os.path.exists(path):
        logger.warning('locale file not found: %s', path)
        _cache[locale] = {}
        return {}

    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    _cache[locale] = data
    return data


def available_locales() -> list:
    """liste des langues disponibles (nom des fichiers locales/*.yaml, sans extension)"""

    if not os.path.isdir(LOCALES_DIR):
        return [DEFAULT_LOCALE]

    return sorted(
        fname[:-5] for fname in os.listdir(LOCALES_DIR) if fname.endswith('.yaml')
    )


def _get_nested(data: dict, dotted_key: str):
    node = data
    for part in dotted_key.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def t(key: str, locale: str = None, **kwargs) -> str:
    """traduit `key` (notation pointee, ex: 'cmd.lang.choose') dans `locale`.
    Si la cle est absente dans cette langue, retombe automatiquement sur
    DEFAULT_LOCALE (comme defaultLanguageOnMissing de telegraf-i18n)"""

    locale = locale or DEFAULT_LOCALE

    value = _get_nested(_load_locale(locale), key)
    if value is None and locale != DEFAULT_LOCALE:
        value = _get_nested(_load_locale(DEFAULT_LOCALE), key)

    if value is None:
        logger.warning("missing translation key '%s' (locale=%s)", key, locale)
        return key

    if kwargs:
        try:
            value = value.format(**kwargs)
        except (KeyError, IndexError):
            pass

    return value


def language_name(locale: str) -> str:
    return t('language_name', locale)


def match_telegram_locale(language_code: str) -> str:
    """fait correspondre le language_code envoye par le client Telegram
    (ex: 'fr', 'pt-br', 'zh-Hans') a l'une de nos langues disponibles.
    Retombe sur DEFAULT_LOCALE si aucune correspondance n'est trouvee.
    C'est ici que se passe la "detection automatique" de la langue."""

    if not language_code:
        return DEFAULT_LOCALE

    language_code = language_code.lower()
    locales = available_locales()

    if language_code in locales:
        return language_code

    short = language_code.split('-')[0]
    if short in locales:
        return short

    return DEFAULT_LOCALE

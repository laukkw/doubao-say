"""Small explicit English/Chinese UI translation layer."""
import os

LANGUAGES = ("system", "en", "zh_CN")
_language = "en"


def resolve_language(language, environ=None):
    if language != "system":
        return language if language in ("en", "zh_CN") else "en"
    env = os.environ if environ is None else environ
    locale = next((env[key] for key in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(key)), "C")
    if locale.split(".")[0].upper() in ("C", "POSIX"):
        return "en"
    for candidate in (env.get("LANGUAGE") or locale).split(":"):
        base = candidate.lower().replace("-", "_").split(".")[0].split("@")[0]
        if base == "zh" or base.startswith("zh_"):
            return "zh_CN"
        if base == "en" or base.startswith("en_") or base in ("c", "posix"):
            return "en"
    return "en"


def set_language(language):
    global _language
    _language = resolve_language(language)


def tr(english, chinese):
    return chinese if _language == "zh_CN" else english

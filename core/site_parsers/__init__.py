"""
Регистрация и управление site-specific адаптерами парсинга.

Этот модуль предоставляет механизм регистрации адаптеров и автоматического
определения подходящего адаптера для заданного URL.
"""

from __future__ import annotations

from typing import Optional, List
from urllib.parse import urlparse

from core.site_parsers.base import SiteParserAdapter

# Список зарегистрированных адаптеров
_registered_adapters: List[SiteParserAdapter] = []


def register_adapter(adapter: SiteParserAdapter) -> None:
    """
    Регистрирует адаптер для использования в системе.

    Args:
        adapter: Экземпляр адаптера для регистрации.
    """
    if adapter not in _registered_adapters:
        _registered_adapters.append(adapter)


def get_adapter_for_url(url: str) -> Optional[SiteParserAdapter]:
    """
    Автоматически определяет подходящий адаптер для заданного URL.

    Проверяет все зарегистрированные адаптеры в порядке регистрации
    и возвращает первый, который может обработать URL.

    Args:
        url: URL для поиска адаптера.

    Returns:
        Подходящий адаптер или None, если ни один адаптер не может обработать URL.
    """
    for adapter in _registered_adapters:
        if adapter.can_handle(url):
            return adapter
    return None


def get_all_adapters() -> List[SiteParserAdapter]:
    """
    Возвращает список всех зарегистрированных адаптеров.

    Returns:
        Список всех зарегистрированных адаптеров.
    """
    return _registered_adapters.copy()


def get_adapter_by_name(name: str) -> Optional[SiteParserAdapter]:
    """
    Находит адаптер по имени.

    Args:
        name: Имя адаптера для поиска.

    Returns:
        Адаптер с указанным именем или None, если не найден.
    """
    for adapter in _registered_adapters:
        if adapter.name == name:
            return adapter
    return None


# Импорт и регистрация адаптеров
from core.site_parsers.fanserials import FanserialsAdapter
from core.site_parsers.beeg import BeegAdapter

# Регистрация адаптеров
_fanserials_adapter = FanserialsAdapter()
register_adapter(_fanserials_adapter)

_beeg_adapter = BeegAdapter()
register_adapter(_beeg_adapter)

# Автоматический импорт всех адаптеров из пакета
# В будущем здесь можно добавить автоматическое обнаружение адаптеров
# через pkgutil или importlib

__all__ = [
    "SiteParserAdapter",
    "register_adapter",
    "get_adapter_for_url",
    "get_all_adapters",
    "get_adapter_by_name",
    "FanserialsAdapter",
    "BeegAdapter",
]


"""
Базовый интерфейс для site-specific адаптеров парсинга.

Адаптеры используются для обработки специфичных сайтов, которые требуют
дополнительной логики парсинга или специальных действий.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple, List, Optional, Dict


class SiteParserAdapter(ABC):
    """
    Базовый класс для site-specific адаптеров парсинга.

    Каждый адаптер должен реализовать методы can_handle и parse для обработки
    специфичных сайтов.
    """

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """
        Проверяет, может ли адаптер обработать данный URL.

        Args:
            url: URL для проверки.

        Returns:
            True, если адаптер может обработать этот URL, иначе False.
        """
        pass

    @abstractmethod
    def parse(
        self,
        url: str,
        cookies_path: Optional[str] = None,
        translation_hash: Optional[str] = None,
        proxy: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[str], List[str], Optional[str], Optional[List[Dict[str, str]]], List[Dict[str, str]]]:
        """
        Парсит страницу и возвращает медиа-ссылки.

        Args:
            url: URL веб-страницы для анализа.
            cookies_path: Путь к cookies.txt (формат Netscape) для доступа
                к приватному/региональному контенту при необходимости.
            translation_hash: Идентификатор перевода (значение data-sound-hash),
                если необходимо выбрать конкретную дорожку озвучки.
            proxy: Прокси-конфигурация (опционально).

        Returns:
            Кортеж (hls_urls, file_urls, page_title, translations, hls_streams):
                hls_urls: список абсолютных ссылок на HLS-потоки (.m3u8);
                file_urls: список абсолютных ссылок на обычные видеофайлы;
                page_title: заголовок страницы, если удалось определить;
                translations: список словарей доступных переводов (name/hash/sound_id);
                hls_streams: список словарей с подробностями по HLS (url, quality).

        Raises:
            ValueError: если URL некорректен.
            RuntimeError: если не удалось загрузить или разобрать страницу.
        """
        pass

    @property
    def name(self) -> str:
        """
        Возвращает имя адаптера для отображения в UI/CLI.

        По умолчанию возвращает имя класса.
        """
        return self.__class__.__name__


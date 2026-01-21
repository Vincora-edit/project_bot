"""
Сервис для работы с Битрикс24 API.

Создание задач, получение пользователей и групп.
"""

import aiohttp
from typing import Optional
from datetime import datetime, timedelta

from src.config import settings
from src.utils.logging import get_logger


logger = get_logger(__name__)


class BitrixService:
    """Сервис для работы с Битрикс24."""

    def __init__(self):
        self.webhook_url = settings.bitrix_webhook_url.rstrip('/')
        self._users_cache: dict = {}
        self._groups_cache: list = []

    async def _call_api(self, method: str, params: dict = None) -> dict | None:
        """
        Вызов Битрикс24 REST API.

        Args:
            method: Метод API (например, 'tasks.task.add')
            params: Параметры запроса

        Returns:
            dict: Ответ API или None при ошибке
        """
        if not self.webhook_url:
            logger.error("BITRIX_WEBHOOK_URL не настроен")
            return None

        url = f"{self.webhook_url}/{method}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=params or {}) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'error' in data:
                            logger.error(f"Bitrix API error: {data['error']} - {data.get('error_description', '')}")
                            return None
                        return data
                    else:
                        logger.error(f"Bitrix API HTTP error: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Bitrix API call failed: {e}")
            return None

    async def get_users(self, force_refresh: bool = False) -> list:
        """
        Получить список пользователей Битрикс24.

        Returns:
            list: Список пользователей [{id, name}, ...]
        """
        if self._users_cache and not force_refresh:
            return list(self._users_cache.values())

        result = await self._call_api('user.get', {'ACTIVE': True})
        if not result or 'result' not in result:
            return []

        users = []
        for user in result['result']:
            user_data = {
                'id': user['ID'],
                'name': f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip(),
                'email': user.get('EMAIL', ''),
            }
            self._users_cache[user['ID']] = user_data
            users.append(user_data)

        return users

    async def get_user_by_name(self, name: str) -> dict | None:
        """
        Найти пользователя по имени.

        Args:
            name: Имя или часть имени

        Returns:
            dict: Данные пользователя или None
        """
        users = await self.get_users()
        name_lower = name.lower()

        for user in users:
            if name_lower in user['name'].lower():
                return user

        return None

    async def get_groups(self, force_refresh: bool = False) -> list:
        """
        Получить список групп/проектов.

        Returns:
            list: Список групп [{id, name}, ...]
        """
        if self._groups_cache and not force_refresh:
            return self._groups_cache

        result = await self._call_api('sonet_group.get', {
            'FILTER': {'ACTIVE': 'Y'},
            'SELECT': ['ID', 'NAME']
        })

        if not result or 'result' not in result:
            return []

        self._groups_cache = [
            {'id': g['ID'], 'name': g['NAME']}
            for g in result['result']
        ]

        return self._groups_cache

    async def get_group_by_name(self, name: str) -> dict | None:
        """
        Найти группу по названию.

        Args:
            name: Название или часть названия

        Returns:
            dict: Данные группы или None
        """
        groups = await self.get_groups()
        name_lower = name.lower()

        for group in groups:
            if name_lower in group['name'].lower():
                return group

        return None

    async def create_task(
        self,
        title: str,
        description: str = "",
        responsible_id: int | str = None,
        creator_id: int | str = None,
        group_id: int | str = None,
        deadline: datetime = None,
        priority: int = 1
    ) -> dict | None:
        """
        Создать задачу в Битрикс24.

        Args:
            title: Заголовок задачи
            description: Описание задачи
            responsible_id: ID ответственного (если None — создатель)
            creator_id: ID постановщика
            group_id: ID группы/проекта (опционально)
            deadline: Дедлайн (опционально)
            priority: Приоритет (0=низкий, 1=средний, 2=высокий)

        Returns:
            dict: Данные созданной задачи или None
        """
        # Если ответственный не указан — назначаем создателю
        if responsible_id is None:
            responsible_id = creator_id or 1

        fields = {
            'TITLE': title,
            'DESCRIPTION': description,
            'RESPONSIBLE_ID': str(responsible_id),
            'PRIORITY': str(priority),
        }

        if creator_id:
            fields['CREATED_BY'] = str(creator_id)

        if group_id:
            fields['GROUP_ID'] = str(group_id)

        if deadline:
            fields['DEADLINE'] = deadline.strftime('%Y-%m-%dT%H:%M:%S')

        result = await self._call_api('tasks.task.add', {'fields': fields})

        if result and 'result' in result:
            task_data = result['result'].get('task', {})
            task_id = task_data.get('id') or result['result'].get('id')
            logger.info(f"Создана задача в Битрикс24: {task_id} - {title}")
            return {
                'id': task_id,
                'title': title,
                'responsible_id': responsible_id,
                'group_id': group_id,
            }

        return None

    async def get_task(self, task_id: int | str) -> dict | None:
        """
        Получить задачу по ID.

        Args:
            task_id: ID задачи

        Returns:
            dict: Данные задачи или None
        """
        result = await self._call_api('tasks.task.get', {
            'taskId': str(task_id),
            'select': ['ID', 'TITLE', 'DESCRIPTION', 'STATUS', 'RESPONSIBLE_ID', 'GROUP_ID', 'DEADLINE']
        })

        if result and 'result' in result:
            return result['result'].get('task')

        return None


# Глобальный экземпляр
bitrix_service = BitrixService()

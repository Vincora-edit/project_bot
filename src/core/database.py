"""
Работа с базой данных Supabase.

Обёртка над Supabase клиентом с типизацией и обработкой ошибок.
"""

from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client

from src.config import settings
from src.utils.logging import get_logger


logger = get_logger(__name__)


class Database:
    """Класс для работы с Supabase."""

    def __init__(self):
        self._client: Client | None = None

    @property
    def client(self) -> Client:
        """Ленивая инициализация клиента."""
        if self._client is None:
            self._client = create_client(settings.supabase_url, settings.supabase_key)
        return self._client

    # ============ CHAT LOG ============

    def log_message(
        self,
        chat_id: str,
        message_id: int,
        from_id: int,
        from_name: str,
        text: str,
        chat_name: str,
        is_project: bool,
    ) -> dict | None:
        """
        Логирует сообщение в БД.

        Returns:
            dict | None: Созданная запись или None при ошибке/дубликате
        """
        try:
            thread_key = f"{chat_id}:{message_id}"

            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "chat_name": chat_name,
                "chat_id": chat_id,
                "message_id": message_id,
                "thread_key": thread_key,
                "from_id": from_id,
                "from_name": from_name,
                "is_project": is_project,
                "project_id": from_id if is_project else None,
                "text": text,
                "status": "logged",
            }

            result = self.client.table("chat_log").insert(data).execute()
            return result.data[0] if result.data else None

        except Exception as e:
            # Игнорируем дубликаты по thread_key
            if "duplicate key value" in str(e).lower() or "23505" in str(e):
                return None
            logger.error(f"Error logging message: {e}")
            return None

    def update_message_status(
        self,
        log_id: int,
        status: str,
        **kwargs: Any
    ) -> bool:
        """Обновляет статус сообщения."""
        try:
            data = {"status": status, **kwargs}
            self.client.table("chat_log").update(data).eq("id", log_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating message status: {e}")
            return False

    def get_message_by_id(self, log_id: int) -> dict | None:
        """Получает сообщение по ID."""
        try:
            result = self.client.table("chat_log").select("*").eq("id", log_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting message: {e}")
            return None

    def get_recent_messages(
        self,
        chat_id: str,
        before_message_id: int,
        limit: int = 5
    ) -> list[dict]:
        """Получает последние сообщения из чата для контекста."""
        try:
            result = (
                self.client.table("chat_log")
                .select("from_name, text, is_project, timestamp")
                .eq("chat_id", chat_id)
                .lt("message_id", before_message_id)
                .order("message_id", desc=True)
                .limit(limit)
                .execute()
            )
            messages = result.data or []
            messages.reverse()  # Старые первыми
            return messages
        except Exception as e:
            logger.error(f"Error getting recent messages: {e}")
            return []

    def find_project_answer(
        self,
        chat_id: str,
        after_message_id: int
    ) -> dict | None:
        """Ищет ответ проджекта после указанного сообщения."""
        try:
            result = (
                self.client.table("chat_log")
                .select("*")
                .eq("chat_id", chat_id)
                .eq("is_project", True)
                .gt("message_id", after_message_id)
                .order("message_id", desc=False)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error finding project answer: {e}")
            return None

    # ============ CHAT OWNERS ============

    def get_chat_owner(self, chat_id: str) -> dict | None:
        """Получает владельца чата."""
        try:
            result = (
                self.client.table("chat_owners")
                .select("*")
                .eq("chat_id", chat_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting chat owner: {e}")
            return None

    def upsert_chat_owner(
        self,
        chat_id: str,
        chat_name: str,
        project_id: int,
        project_name: str
    ) -> bool:
        """Создаёт или обновляет владельца чата."""
        try:
            existing = self.get_chat_owner(chat_id)

            payload = {
                "chat_id": chat_id,
                "chat_name": chat_name,
                "project_id": project_id,
                "project_name": project_name,
                "assigned_at": datetime.now().isoformat(),
            }

            if existing:
                self.client.table("chat_owners").update(payload).eq("chat_id", chat_id).execute()
            else:
                self.client.table("chat_owners").insert(payload).execute()

            return True
        except Exception as e:
            logger.error(f"Error upserting chat owner: {e}")
            return False

    def get_all_chat_owners(self) -> list[dict]:
        """Получает всех владельцев чатов."""
        try:
            result = self.client.table("chat_owners").select("*").execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting all chat owners: {e}")
            return []

    # ============ DEALS ============

    def get_deal(self, deal_id: str) -> dict | None:
        """Получает сделку по ID."""
        try:
            result = self.client.table("deals").select("*").eq("deal_id", deal_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting deal: {e}")
            return None

    def upsert_deal(self, deal_data: dict) -> bool:
        """Создаёт или обновляет сделку."""
        try:
            existing = self.get_deal(deal_data["deal_id"])

            deal_data["updated_at"] = datetime.now(timezone.utc).isoformat()

            if existing:
                self.client.table("deals").update(deal_data).eq("deal_id", deal_data["deal_id"]).execute()
            else:
                deal_data["created_at"] = datetime.now(timezone.utc).isoformat()
                self.client.table("deals").insert(deal_data).execute()

            return True
        except Exception as e:
            logger.error(f"Error upserting deal: {e}")
            return False

    def get_deals_by_chat(self, chat_id: str) -> list[dict]:
        """Получает все сделки для чата."""
        try:
            result = self.client.table("deals").select("*").eq("chat_id", chat_id).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting deals by chat: {e}")
            return []

    def delete_deal(self, deal_id: str) -> bool:
        """Удаляет сделку."""
        try:
            self.client.table("deals").delete().eq("deal_id", deal_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error deleting deal: {e}")
            return False

    # ============ STAGE ACTIONS ============

    def get_stage_actions(self, stage_id: str, service_type: str) -> list[dict]:
        """Получает действия для стадии."""
        try:
            result = (
                self.client.table("stage_actions")
                .select("*")
                .eq("stage_id", stage_id)
                .eq("service_type", service_type)
                .eq("is_active", True)
                .order("priority")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting stage actions: {e}")
            return []

    def create_stage_action(self, action_data: dict) -> bool:
        """Создаёт действие для стадии."""
        try:
            self.client.table("stage_actions").insert(action_data).execute()
            return True
        except Exception as e:
            logger.error(f"Error creating stage action: {e}")
            return False

    # ============ NPS QUEUE ============

    def add_to_nps_queue(self, nps_data: dict) -> bool:
        """Добавляет запись в очередь NPS."""
        try:
            self.client.table("nps_queue").insert(nps_data).execute()
            return True
        except Exception as e:
            logger.error(f"Error adding to NPS queue: {e}")
            return False

    def get_pending_nps(self) -> list[dict]:
        """Получает NPS-записи готовые к отправке."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = (
                self.client.table("nps_queue")
                .select("*")
                .is_("sent_at", "null")
                .lte("send_at", now)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Error getting pending NPS: {e}")
            return []

    def mark_nps_sent(self, nps_id: int) -> bool:
        """Помечает NPS как отправленный."""
        try:
            self.client.table("nps_queue").update({
                "sent_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", nps_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking NPS sent: {e}")
            return False

    # ============ CLIENT KNOWLEDGE ============

    def get_client_knowledge(self, chat_id: str) -> dict | None:
        """Получает базу знаний по клиенту."""
        try:
            result = (
                self.client.table("client_knowledge")
                .select("*")
                .eq("chat_id", chat_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting client knowledge: {e}")
            return None

    def upsert_client_knowledge(self, chat_id: str, **kwargs) -> bool:
        """Создаёт или обновляет базу знаний по клиенту."""
        try:
            existing = self.get_client_knowledge(chat_id)

            data = {"chat_id": chat_id, **kwargs}
            data["updated_at"] = datetime.now(timezone.utc).isoformat()

            if existing:
                self.client.table("client_knowledge").update(data).eq("chat_id", chat_id).execute()
            else:
                data["created_at"] = datetime.now(timezone.utc).isoformat()
                self.client.table("client_knowledge").insert(data).execute()

            return True
        except Exception as e:
            logger.error(f"Error upserting client knowledge: {e}")
            return False

    def update_client_field(self, chat_id: str, field: str, value: str) -> bool:
        """Обновляет одно поле базы знаний."""
        return self.upsert_client_knowledge(chat_id, **{field: value})

    def append_client_note(self, chat_id: str, note: str) -> bool:
        """Добавляет заметку к существующим."""
        try:
            existing = self.get_client_knowledge(chat_id)
            current_notes = existing.get("notes", "") if existing else ""

            if current_notes:
                new_notes = f"{current_notes}\n---\n{note}"
            else:
                new_notes = note

            return self.upsert_client_knowledge(chat_id, notes=new_notes)
        except Exception as e:
            logger.error(f"Error appending client note: {e}")
            return False


# Глобальный экземпляр
db = Database()

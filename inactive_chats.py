

# ========== НАПОМИНАНИЕ О НЕАКТИВНЫХ ЧАТАХ ==========

async def check_inactive_chats_job():
    """
    Проверка неактивных чатов в 12:00.
    Если в чате не было сообщений весь вчерашний день — напоминаем проджекту.
    """
    print(f"🔔 Запуск проверки неактивных чатов: {now_local().isoformat()}")

    try:
        # Получаем все чаты с назначенными проджектами
        chats_resp = supabase.table("chat_owners").select("*").execute()
        chats = chats_resp.data or []

        if not chats:
            print("📭 Нет чатов для проверки")
            return

        # Вычисляем вчерашний день (начало и конец)
        today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = (today - timedelta(days=1)).isoformat()
        yesterday_end = today.isoformat()

        print(f"📅 Проверяем сообщения за: {yesterday_start} - {yesterday_end}")

        for chat in chats:
            chat_id = chat.get("chat_id")
            chat_name = chat.get("chat_name", "Unknown")
            project_id = chat.get("project_id")
            project_name = chat.get("project_name", "Unknown")

            if not chat_id or not project_id:
                continue

            try:
                # Проверяем, были ли сообщения вчера
                messages = (
                    supabase.table("chat_log")
                    .select("id")
                    .eq("chat_id", chat_id)
                    .gte("timestamp", yesterday_start)
                    .lt("timestamp", yesterday_end)
                    .limit(1)
                    .execute()
                )

                if messages.data:
                    # Были сообщения — всё ок
                    print(f"✅ {chat_name}: активность есть")
                    continue

                # Сообщений не было — отправляем напоминание проджекту
                print(f"⚠️ {chat_name}: нет активности вчера")

                reminder_text = (
                    f"📢 Напоминание о чате

"
                    f"🏷️ Чат: {chat_name}
"
                    f"📅 Вчера в этом чате не было сообщений.

"
                    f"💡 Напиши клиенту о ходе работы или узнай, всё ли в порядке."
                )

                # Отправляем проджекту
                try:
                    await bot.send_message(int(project_id), reminder_text)
                    print(f"📨 Напоминание отправлено проджекту {project_name} ({project_id})")
                except Exception as e:
                    print(f"❌ Не удалось отправить проджекту {project_id}: {e}")

                # Также отправляем владельцу (если это не тот же человек)
                if int(project_id) != OWNER_ID:
                    try:
                        await bot.send_message(OWNER_ID, f"[Копия для владельца]

{reminder_text}")
                    except Exception as e:
                        print(f"❌ Не удалось отправить владельцу: {e}")

            except Exception as e:
                print(f"❌ Ошибка проверки чата {chat_name}: {e}")

        print(f"✅ Проверка неактивных чатов завершена")

    except Exception as e:
        print(f"❌ Ошибка check_inactive_chats_job: {e}")

"""Tests E5 — búsqueda del historial de chat sobre texto plano (search_text).

Cubren el cambio en database.py: la columna derivada `search_text`, el
mantenimiento en create/update, el backfill de conversaciones previas y la
migración ALTER TABLE idempotente. El ruido que se elimina: coincidencias con
la estructura del JSON ("role"/"content"/"text") y con data-URI de imágenes.
"""

import uuid

import database


async def _create_conversation(client, messages, title=None):
    payload = {
        "model_name": "test-model",
        "endpoint": "http://127.0.0.1:1",
        "messages": messages,
    }
    if title is not None:
        payload["title"] = title
    res = await client.post("/api/chat/conversations", json=payload)
    assert res.status_code == 201
    return res.json()["id"]


async def _search(client, term):
    res = await client.get("/api/chat/conversations", params={"search": term})
    assert res.status_code == 200
    return res.json()


async def test_search_finds_content_and_title(client):
    await _create_conversation(
        client,
        [{"role": "user", "content": "prepara una salsa de tomate casera"}],
    )
    await _create_conversation(
        client,
        [{"role": "user", "content": "sin relacion"}],
        title="Recetas de la abuela",
    )

    hits = await _search(client, "salsa de tomate")
    assert len(hits) == 1
    assert "salsa de tomate" in (await client.get(f"/api/chat/conversations/{hits[0]['id']}")).json()["messages"][0]["content"]

    hits = await _search(client, "abuela")
    assert len(hits) == 1  # coincide por el título

    hits = await _search(client, "TOMATE")  # case-insensitive ASCII
    assert len(hits) == 1


async def test_search_ignores_json_structure_noise(client):
    await _create_conversation(
        client,
        [{"role": "user", "content": "historia sobre un faro"}],
    )
    # Antes (ilike sobre el JSON completo) estos términos coincidian con la
    # estructura y devolvían TODAS las conversaciones.
    for term in ("user", "role", "content", "assistant"):
        assert await _search(client, term) == []


async def test_search_skips_image_parts_and_data_uris(client):
    conv_id = await _create_conversation(
        client,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe este gato"},
                    {"type": "image_ref", "id": "att-123"},
                ],
            },
            {
                "role": "assistant",
                "content": "es un gato",
                "thinking": "mirada serena",
            },
            {
                "role": "user",
                "content": "data:image/png;base64,iVBORw0KGgoAAA",
            },
        ],
    )
    assert len(await _search(client, "describe este")) == 1  # texto dentro de partes
    assert len(await _search(client, "mirada serena")) == 1  # thinking sí es buscable
    assert await _search(client, "base64") == []  # los data-URI no
    assert await _search(client, "image_ref") == []  # ni las claves de las partes
    assert await _search(client, "att-123") == []


async def test_update_reindexes_searchable_text(client):
    conv_id = await _create_conversation(
        client,
        [{"role": "user", "content": "texto original zzunico1"}],
        title="título fijo",
    )
    assert len(await _search(client, "original zzunico1")) == 1

    res = await client.put(
        f"/api/chat/conversations/{conv_id}",
        json={"messages": [{"role": "user", "content": "texto reemplazado zzunico2"}]},
    )
    assert res.status_code == 200
    assert await _search(client, "original zzunico1") == []
    assert len(await _search(client, "reemplazado zzunico2")) == 1


async def test_backfill_indexes_conversations_previas(client):
    # Simula una fila creada antes de E5: messages con search_text en NULL.
    async with database.get_session() as session:
        session.add(
            database.ChatConversationRow(
                id=uuid.uuid4().hex,
                title="conversación legacy",
                created_at=database.utc_now_iso(),
                updated_at=database.utc_now_iso(),
                messages=database._dumps(
                    [{"role": "user", "content": "contenido anterior a la migración"}]
                ),
                message_count=1,
            )
        )
        await session.commit()

    filled = await database._backfill_chat_search_text()
    assert filled >= 1
    assert len(await _search(client, "anterior a la migración")) == 1

    # Idempotencia: las filas ya indexadas (cadena, posiblemente vacía) no se
    # re-procesan en arranques siguientes.
    assert await database._backfill_chat_search_text() == 0


async def test_migration_agrega_columna_en_db_vieja():
    # Simula el schema previo a E5 y verifica que ensure() lo migra.
    engine = database.get_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "ALTER TABLE chat_conversations DROP COLUMN search_text"
        )
    await database._ensure_chat_search_text(engine)
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(chat_conversations)")
        columns = {row[1] for row in result.fetchall()}
    assert "search_text" in columns


def test_extract_search_text_rules():
    text = database._extract_chat_search_text(
        [
            {"role": "user", "content": "hola MUNDO"},
            {"role": "assistant", "content": [{"type": "text", "text": "parte textual"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}], "thinking": "piensa"},
            {"role": "system", "content": "data:text/plain,esto-no-deberia-pasar-pero-es-string"},
            {"role": "user", "content": None, "metrics": {"context_tokens": 5}},
        ]
    )
    assert "hola MUNDO" in text
    assert "parte textual" in text
    assert "piensa" in text
    assert "context_tokens" not in text  # las métricas no son texto buscable
    assert "AAAA" not in text  # el data-URI del image_url se saltea por type

# language: Python 3.10+
import os
import re
import logging
import aiohttp
from urllib.parse import quote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "8848431247:AAExAxXQh07SzCQT5owDhIe4xc8dtl4SATQ"

STREAM_BASE = "https://ww3.animeonline.ninja"
ANILIST = "https://graphql.anilist.co"
EPS_PER_PAGE = 20

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

user_data: dict[int, dict] = {}


async def anilist_query(query: str, variables: dict | None = None):
    payload = {"query": query, "variables": variables or {}}
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(ANILIST, json=payload) as r:
                if r.status != 200:
                    logger.warning(f"AniList status {r.status}")
                    return None
                data = await r.json()
                return data.get("data")
    except Exception as e:
        logger.warning(f"Error AniList: {e}")
        return None


def slugify(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    t = re.sub(r"[\s_]+", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return t


def episode_url(title: str, ep: int) -> str:
    slug = slugify(title)
    return f"{STREAM_BASE}/episodio/{slug}-cap-{ep}/"


def anime_search_url(title: str) -> str:
    return f"{STREAM_BASE}/?s={quote(title)}"


def normalize_anime(m: dict) -> dict:
    """Convierte respuesta AniList a formato simple."""
    titles = m.get("title") or {}
    title = titles.get("romaji") or titles.get("english") or titles.get("native") or "Sin título"
    desc = m.get("description") or "Sin sinopsis"
    # quitar HTML simple de la descripción
    desc = re.sub(r"<br\s*/?>", "\n", desc, flags=re.I)
    desc = re.sub(r"<[^>]+>", "", desc)
    score = m.get("averageScore")
    if score is not None:
        score = round(score / 10, 1)  # AniList usa 0-100
    return {
        "title": title,
        "title_english": titles.get("english"),
        "episodes": m.get("episodes") or 12,
        "score": score or "N/A",
        "status": m.get("status") or "?",
        "year": m.get("seasonYear") or "?",
        "synopsis": desc,
        "image": (m.get("coverImage") or {}).get("large"),
        "url": m.get("siteUrl") or "",
    }


SEARCH_QUERY = """
query ($search: String) {
  Page(perPage: 6) {
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
      id
      title { romaji english native }
      episodes
      averageScore
      status
      seasonYear
      description(asHtml: false)
      coverImage { large }
      siteUrl
    }
  }
}
"""

RANDOM_QUERY = """
query {
  Page(page: 1, perPage: 1) {
    media(type: ANIME, sort: POPULARITY_DESC) {
      id
      title { romaji english native }
      episodes
      averageScore
      status
      seasonYear
      description(asHtml: false)
      coverImage { large }
      siteUrl
    }
  }
}
"""

TOP_QUERY = """
query {
  Page(page: 1, perPage: 10) {
    media(type: ANIME, sort: SCORE_DESC) {
      id
      title { romaji english native }
      episodes
      averageScore
      status
      seasonYear
      description(asHtml: false)
      coverImage { large }
      siteUrl
    }
  }
}
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎬 <b>Anime Links</b>\n\n"
        "Comandos:\n"
        "/search nombre — busca anime\n"
        "/random — anime popular\n"
        "/top — top 10\n\n"
        "Ejemplo:\n"
        "<code>/search one piece</code>\n\n"
        "Elige anime → elige capítulo → recibes el enlace."
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usa: /search nombre del anime")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"Buscando «{query}»...")

    data = await anilist_query(SEARCH_QUERY, {"search": query})
    if not data or not data.get("Page") or not data["Page"].get("media"):
        await update.message.reply_text(
            "No pude conectar con AniList o no hay resultados.\n"
            "Revisa tu internet e intenta de nuevo."
        )
        return

    results = [normalize_anime(m) for m in data["Page"]["media"]]
    uid = update.effective_user.id
    user_data[uid] = {"results": results}

    lines = [f"Resultados para <b>{query}</b>:\n"]
    keyboard = []
    for i, a in enumerate(results):
        lines.append(
            f"{i + 1}. <b>{a['title']}</b> ({a['year']}) ⭐ {a['score']} · {a['episodes']} eps"
        )
        keyboard.append(
            [InlineKeyboardButton(f"▶ {a['title'][:40]}", callback_data=f"anime:{i}")]
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query
    uid = query.from_user.id
    data = user_data.get(uid)

    if not data or "anime" not in data:
        await query.edit_message_text("Sesión expirada. Usa /search de nuevo.")
        return

    anime = data["anime"]
    title = anime["title"]
    total = data.get("total_eps") or 12
    if total > 500:
        total = 500

    start_ep = page * EPS_PER_PAGE + 1
    end_ep = min(start_ep + EPS_PER_PAGE - 1, total)

    lines = [
        f"<b>{title}</b>",
        f"Capítulos {start_ep}–{end_ep} de {total}",
        "",
        "Elige un capítulo:",
    ]

    keyboard = []
    row = []
    for ep in range(start_ep, end_ep + 1):
        row.append(InlineKeyboardButton(str(ep), callback_data=f"ep:{ep}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Anterior", callback_data=f"page:{page - 1}"))
    if end_ep < total:
        nav.append(InlineKeyboardButton("Siguiente ➡", callback_data=f"page:{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append(
        [InlineKeyboardButton("🔍 Buscar en el sitio", url=anime_search_url(title))]
    )

    text = "\n".join(lines)
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        await query.message.reply_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def anime_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        idx = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("Selección inválida.")
        return

    uid = query.from_user.id
    data = user_data.get(uid)
    if not data or "results" not in data or idx >= len(data["results"]):
        await query.edit_message_text("Búsqueda expirada. Usa /search de nuevo.")
        return

    anime = data["results"][idx]
    total_eps = anime.get("episodes") or 12
    if not isinstance(total_eps, int) or total_eps < 1:
        total_eps = 12

    user_data[uid]["anime"] = anime
    user_data[uid]["total_eps"] = total_eps

    synopsis = (anime.get("synopsis") or "Sin sinopsis")[:250]
    caption = (
        f"<b>{anime['title']}</b>\n"
        f"⭐ {anime['score']}  |  📺 {total_eps} eps  |  {anime['status']}\n\n"
        f"{synopsis}...\n\n"
        f"Elige el capítulo abajo 👇"
    )

    img = anime.get("image")
    if img:
        await query.message.reply_photo(photo=img, caption=caption, parse_mode="HTML")
    else:
        await query.message.reply_text(caption, parse_mode="HTML")

    await show_episodes(update, context, page=0)


async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return
    await show_episodes(update, context, page=page)


async def episode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        ep = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.message.reply_text("Capítulo inválido.")
        return

    uid = query.from_user.id
    data = user_data.get(uid)
    if not data or "anime" not in data:
        await query.message.reply_text("Sesión expirada. Usa /search de nuevo.")
        return

    anime = data["anime"]
    title = anime["title"]
    link = episode_url(title, ep)
    search = anime_search_url(title)

    text = (
        f"<b>{title}</b> — Capítulo {ep}\n\n"
        f"🔗 <a href='{link}'>Ver capítulo {ep}</a>\n\n"
        f"Si no abre, prueba la búsqueda:\n"
        f"🔍 <a href='{search}'>Buscar «{title}» en el sitio</a>"
    )
    await query.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=False)


async def random_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Buscando anime...")
    data = await anilist_query(RANDOM_QUERY)
    if not data or not data.get("Page") or not data["Page"].get("media"):
        await update.message.reply_text("No pude conectar. Intenta de nuevo.")
        return

    anime = normalize_anime(data["Page"]["media"][0])
    total_eps = anime.get("episodes") or 12
    if not isinstance(total_eps, int) or total_eps < 1:
        total_eps = 12

    uid = update.effective_user.id
    user_data[uid] = {"results": [anime], "anime": anime, "total_eps": total_eps}

    synopsis = (anime.get("synopsis") or "Sin sinopsis")[:250]
    caption = (
        f"<b>{anime['title']}</b>\n"
        f"⭐ {anime['score']}  |  📺 {total_eps} eps\n\n"
        f"{synopsis}...\n\n"
        f"Elige el capítulo abajo 👇"
    )

    img = anime.get("image")
    if img:
        await update.message.reply_photo(photo=img, caption=caption, parse_mode="HTML")
    else:
        await update.message.reply_text(caption, parse_mode="HTML")

    keyboard = []
    row = []
    max_show = min(total_eps, EPS_PER_PAGE)
    for ep in range(1, max_show + 1):
        row.append(InlineKeyboardButton(str(ep), callback_data=f"ep:{ep}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    if total_eps > EPS_PER_PAGE:
        keyboard.append([InlineKeyboardButton("Siguiente ➡", callback_data="page:1")])
    keyboard.append(
        [InlineKeyboardButton("🔍 Buscar en el sitio", url=anime_search_url(anime["title"]))]
    )

    await update.message.reply_text(
        f"Capítulos 1–{max_show} de {total_eps}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cargando top...")
    data = await anilist_query(TOP_QUERY)
    if not data or not data.get("Page") or not data["Page"].get("media"):
        await update.message.reply_text("No pude cargar el top.")
        return

    results = [normalize_anime(m) for m in data["Page"]["media"]]
    uid = update.effective_user.id
    user_data[uid] = {"results": results}

    lines = ["🏆 <b>Top 10</b>\n"]
    keyboard = []
    for i, a in enumerate(results):
        lines.append(f"{i + 1}. <b>{a['title']}</b> — ⭐ {a['score']}")
        keyboard.append(
            [InlineKeyboardButton(f"▶ {i + 1}. {a['title'][:35]}", callback_data=f"anime:{i}")]
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN.startswith("PON_AQUI"):
        print("ERROR: Pon tu TELEGRAM_TOKEN")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("random", random_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CallbackQueryHandler(anime_callback, pattern=r"^anime:"))
    app.add_handler(CallbackQueryHandler(page_callback, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(episode_callback, pattern=r"^ep:"))
    print("Bot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
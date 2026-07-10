import concurrent.futures
import json
import logging
import math
import os
import tempfile
import urllib.parse
import urllib.request
from html import escape as html_escape
from types import SimpleNamespace

# noinspection PyPackageRequirements
from telegram import ChatAction, ParseMode, Update
# noinspection PyPackageRequirements
from telegram.error import BadRequest, TelegramError
# noinspection PyPackageRequirements
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackContext,
    Filters
)

from bot import stickersbot
from bot.strings import Strings
from ..conversation_statuses import Status
from ..fallback_commands import cancel_command
from ...utils import decorators
from ...utils import converter
from ...utils import wastickers

logger = logging.getLogger(__name__)

# nombre de stickers telecharges+convertis EN PARALLELE. ffmpeg/le telechargement
# liberent le GIL pendant l'essentiel de leur travail, donc des threads suffisent
# (pas besoin de multiprocessing). Augmente si le serveur a plus de CPU/bande passante,
# reduis si tu vois des timeouts ou une surcharge memoire/CPU sur de gros packs animes.
MAX_PARALLEL_CONVERSIONS = 8


def _get_sticker_set_raw(context: CallbackContext, name: str) -> SimpleNamespace:
    """recupere un sticker set en appelant directement l'API Telegram (urllib),
    en contournant telegram.StickerSet.de_json().

    Necessaire car python-telegram-bot 13.x exige encore les champs is_animated
    et is_video sur l'objet StickerSet, alors que Telegram les a retires de la
    reponse de getStickerSet depuis Bot API 7.2 (juin 2024). Sans ce contournement,
    bot.get_sticker_set() plante avec:
    "StickerSet.__init__() missing 2 required positional arguments:
    'is_animated' and 'is_video'"
    Voir: https://github.com/python-telegram-bot/python-telegram-bot/issues/4181
    """

    url = 'https://api.telegram.org/bot{}/getStickerSet?{}'.format(
        context.bot.token, urllib.parse.urlencode({'name': name})
    )

    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode('utf-8'))

    if not payload.get('ok'):
        raise RuntimeError(payload.get('description', 'getStickerSet failed'))

    result = payload['result']

    stickers = [
        SimpleNamespace(
            file_id=s['file_id'],
            file_unique_id=s.get('file_unique_id'),
            is_animated=bool(s.get('is_animated', False)),
            is_video=bool(s.get('is_video', False)),
            emoji=s.get('emoji'),
        )
        for s in result.get('stickers', [])
    ]

    return SimpleNamespace(
        name=result['name'],
        title=result['title'],
        stickers=stickers,
    )


def _download_raw_sticker(context: CallbackContext, sticker) -> str:
    """telecharge un sticker Telegram (tel quel: webp/tgs/webm) dans un fichier
    temporaire et renvoie son chemin. L'appelant est responsable de le supprimer"""

    if sticker.is_animated:
        suffix = '.tgs'
    elif sticker.is_video:
        suffix = '.webm'
    else:
        suffix = '.webp'

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_in:
        input_path = tmp_in.name

    telegram_file = context.bot.get_file(sticker.file_id)
    telegram_file.download(custom_path=input_path)

    return input_path


def _convert_single_sticker(sticker, input_path: str) -> bytes:
    """convertit un sticker brut Telegram (webp/tgs/webm) en webp compatible WhatsApp.
    Leve converter.ConversionError si la conversion echoue (l'appelant doit alors
    sauter ce sticker plutot que de faire echouer tout le pack)"""

    out = tempfile.SpooledTemporaryFile()
    try:
        if sticker.is_animated:
            converter.convert_tgs_to_wa_animated_webp(input_path, out)
        elif sticker.is_video:
            converter.convert_video_to_wa_animated_webp(input_path, out)
        else:
            converter.convert_image_to_wa_static_webp(input_path, out)

        out.seek(0)
        return out.read()
    finally:
        out.close()


def _download_and_convert_one(context: CallbackContext, sticker) -> bytes:
    """telecharge PUIS convertit un seul sticker. Concue pour etre lancee dans un
    thread pool: chaque sticker est independant des autres (fichier temporaire propre,
    aucun etat partage), donc plusieurs peuvent tourner en parallele sans se marcher dessus"""

    input_path = None
    try:
        input_path = _download_raw_sticker(context, sticker)
        return _convert_single_sticker(sticker, input_path)
    finally:
        if input_path and os.path.exists(input_path):
            os.remove(input_path)


@decorators.action(ChatAction.TYPING)
@decorators.restricted
@decorators.failwithmessage
@decorators.logconversation
def on_import_command(update: Update, _):
    logger.info('/import')

    update.message.reply_text(Strings.IMPORT_PACK_SELECT)

    return Status.IMPORT_WAITING_STICKER


@decorators.action(ChatAction.UPLOAD_DOCUMENT)
@decorators.failwithmessage
@decorators.logconversation
def on_sticker_receive(update: Update, context: CallbackContext):
    logger.info('user sent a sticker from the pack to import')

    if not update.message.sticker.set_name:
        update.message.reply_text(Strings.IMPORT_PACK_NO_PACK)
        return Status.IMPORT_WAITING_STICKER

    sticker_set = _get_sticker_set_raw(context, update.message.sticker.set_name)

    update.message.reply_text(Strings.IMPORT_PACK_PROCESSING)

    total = len(sticker_set.stickers)
    files_count = wastickers.files_count_for(total)
    # estimation basee sur un debit approximatif de MAX_PARALLEL_CONVERSIONS/2 stickers
    # convertis par seconde (grace au parallelisme), au lieu d'un traitement un par un
    estimated_stickers_per_minute = 30 * MAX_PARALLEL_CONVERSIONS
    estimated_minutes = max(1, math.ceil(total / estimated_stickers_per_minute))

    update.message.reply_text(Strings.IMPORT_PACK_STARTING.format(estimated_minutes))

    update.message.reply_html(
        Strings.IMPORT_PACK_DETAILS.format(html_escape(sticker_set.title), total, files_count)
    )

    converted_by_index = {}
    skipped_stickers = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_CONVERSIONS) as executor:
        future_to_index = {
            executor.submit(_download_and_convert_one, context, sticker): index
            for index, sticker in enumerate(sticker_set.stickers)
        }

        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                converted_by_index[index] = future.result()
            except Exception:
                logger.warning('skipping sticker #%d of %s: conversion failed', index, sticker_set.name,
                                exc_info=True)
                skipped_stickers += 1

    # les threads terminent dans un ordre imprevisible: on retrie par index d'origine
    # pour garder le meme ordre que dans le pack Telegram
    converted_stickers = [converted_by_index[i] for i in sorted(converted_by_index)]

    if not converted_stickers:
        update.message.reply_text(Strings.IMPORT_PACK_NO_STICKERS_CONVERTED)
        return ConversationHandler.END

    tray_icon_png = wastickers.build_tray_icon_png(converted_stickers[0])

    wa_files = wastickers.build_wastickers_files(
        title=sticker_set.title,
        author=f'@{context.bot.username}',
        stickers_webp=converted_stickers,
        tray_icon_png=tray_icon_png,
    )

    complete_text = Strings.IMPORT_PACK_COMPLETE.format(len(wa_files))
    if skipped_stickers:
        complete_text += Strings.IMPORT_PACK_SKIPPED_STICKERS.format(skipped_stickers)

    update.message.reply_text(complete_text)

    for filename, buf in wa_files:
        try:
            update.message.reply_document(buf, filename=filename)
        except (TelegramError, BadRequest) as e:
            logger.error('error while sending a .wastickers file: %s', str(e))

    return ConversationHandler.END


@decorators.action(ChatAction.TYPING)
@decorators.failwithmessage
@decorators.logconversation
def on_ongoing_async_operation(update: Update, _):
    logger.info('user sent a message while the import is ongoing')

    update.message.reply_text(Strings.IMPORT_ONGOING)


stickersbot.add_handler(ConversationHandler(
    name='import_command',
    persistent=False,
    entry_points=[CommandHandler(['import', 'towa', 'importpack'], on_import_command)],
    states={
        Status.IMPORT_WAITING_STICKER: [
            MessageHandler(Filters.sticker, on_sticker_receive, run_async=True),
        ],
        ConversationHandler.WAITING: [MessageHandler(Filters.all, on_ongoing_async_operation)]
    },
    fallbacks=[CommandHandler(['cancel', 'c', 'done', 'd'], cancel_command)],
))

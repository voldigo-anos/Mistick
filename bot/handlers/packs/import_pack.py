import logging
import math
import os
import tempfile
from html import escape as html_escape

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

    sticker_set = context.bot.get_sticker_set(update.message.sticker.set_name)

    update.message.reply_text(Strings.IMPORT_PACK_PROCESSING)

    total = len(sticker_set.stickers)
    files_count = wastickers.files_count_for(total)
    estimated_minutes = max(1, math.ceil(total / 60))

    update.message.reply_text(Strings.IMPORT_PACK_STARTING.format(estimated_minutes))

    update.message.reply_html(
        Strings.IMPORT_PACK_DETAILS.format(html_escape(sticker_set.title), total, files_count)
    )

    converted_stickers = []  # liste de bytes webp, dans l'ordre du pack
    skipped_stickers = 0

    for sticker in sticker_set.stickers:
        input_path = None
        try:
            input_path = _download_raw_sticker(context, sticker)
            webp_bytes = _convert_single_sticker(sticker, input_path)
            converted_stickers.append(webp_bytes)
        except Exception:
            logger.warning('skipping a sticker of %s: conversion failed', sticker_set.name, exc_info=True)
            skipped_stickers += 1
            continue
        finally:
            if input_path and os.path.exists(input_path):
                os.remove(input_path)

    if not converted_stickers:
        update.message.reply_text(
            "❌ I couldn't convert any sticker from this pack. The pack might only contain formats "
            "that aren't supported on this server."
        )
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

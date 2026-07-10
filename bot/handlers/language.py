import logging

# noinspection PyPackageRequirements
from telegram import ChatAction, InlineKeyboardButton, InlineKeyboardMarkup, Update
# noinspection PyPackageRequirements
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, MessageHandler, Filters

from bot import stickersbot, i18n
from bot.database.models.user import User
from bot.utils import decorators

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = 'set_locale:'


def _build_language_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for locale in i18n.available_locales():
        row.append(InlineKeyboardButton(i18n.language_name(locale), callback_data=f'{CALLBACK_PREFIX}{locale}'))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(buttons)


@decorators.failwithmessage
def on_auto_detect_locale(update: Update, _):
    """s'execute en arriere-plan (group=-1) sur CHAQUE message/callback recu, AVANT
    tout autre handler: si l'utilisateur n'a encore jamais eu de langue enregistree,
    la deduit automatiquement de son client Telegram (update.effective_user.language_code)
    et l'enregistre. Ne bloque jamais le traitement normal de la commande."""

    user = update.effective_user
    if not user:
        return

    if User.get_locale(user.id) is not None:
        return  # deja detectee/choisie, rien a faire

    detected = User.get_or_detect_locale(user.id, user.language_code)
    logger.info('auto-detected locale <%s> for user <%d> (telegram language_code=%s)',
                detected, user.id, user.language_code)


@decorators.action(ChatAction.TYPING)
@decorators.failwithmessage
def on_language_command(update: Update, _):
    logger.info('/language')

    update.message.reply_text(
        i18n.t('cmd.lang.choose', User.get_locale(update.effective_user.id)),
        reply_markup=_build_language_keyboard()
    )


@decorators.failwithmessage
def on_language_selected(update: Update, _):
    query = update.callback_query
    locale = query.data[len(CALLBACK_PREFIX):]

    if locale not in i18n.available_locales():
        query.answer()
        return

    User.set_locale(update.effective_user.id, locale)

    query.answer(i18n.language_name(locale))
    query.edit_message_text(i18n.t('cmd.lang.chosen', locale, language=i18n.language_name(locale)))


# group=-1: s'execute avant tous les autres handlers (create/add/export/...),
# pour n'importe quel type de mise a jour, sans jamais interrompre leur execution
stickersbot.add_handler(MessageHandler(Filters.all, on_auto_detect_locale), group=-1)
stickersbot.add_handler(CallbackQueryHandler(on_auto_detect_locale), group=-1)

stickersbot.add_handler(CommandHandler(['language', 'lang'], on_language_command))
stickersbot.add_handler(CallbackQueryHandler(on_language_selected, pattern=f'^{CALLBACK_PREFIX}'))
  

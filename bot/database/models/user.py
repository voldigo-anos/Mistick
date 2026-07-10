from sqlalchemy import Column, Integer, String

from ..base import Base, engine, session_scope


class User(Base):
    __tablename__ = 'users'

    user_id = Column(Integer, primary_key=True)
    # None = la langue n'a encore jamais ete detectee/choisie pour cet utilisateur
    locale = Column(String, nullable=True)

    def __init__(self, user_id: int, locale: str = None):
        self.user_id = user_id
        self.locale = locale

    @staticmethod
    def get_locale(user_id: int) -> str or None:
        """renvoie la locale enregistree pour cet utilisateur, ou None si jamais definie
        (dans ce cas l'appelant doit se rabattre sur la detection automatique)"""

        with session_scope() as session:
            user = session.query(User).filter(User.user_id == user_id).one_or_none()
            return user.locale if user else None

    @staticmethod
    def set_locale(user_id: int, locale: str):
        """enregistre (ou met a jour) la locale de cet utilisateur"""

        with session_scope() as session:
            user = session.query(User).filter(User.user_id == user_id).one_or_none()
            if user:
                user.locale = locale
            else:
                session.add(User(user_id=user_id, locale=locale))

    @staticmethod
    def get_or_detect_locale(user_id: int, telegram_language_code: str) -> str:
        """renvoie la locale de l'utilisateur. Si elle n'a jamais ete definie
        (1ere interaction avec le bot), la deduit automatiquement du language_code
        envoye par le client Telegram, l'enregistre, puis la renvoie"""

        from bot import i18n  # import tardif pour eviter un import circulaire

        locale = User.get_locale(user_id)
        if locale:
            return locale

        detected = i18n.match_telegram_locale(telegram_language_code)
        User.set_locale(user_id, detected)
        return detected


Base.metadata.create_all(engine)


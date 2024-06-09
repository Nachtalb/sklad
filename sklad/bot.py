import logging

from telegram import Update
from telegram.ext import Application, ContextTypes

from sklad.db import User
from sklad.twitter import Twitter


class Bot:
    def __init__(self, local_mode: bool = False) -> None:
        self.logger = logging.getLogger(__name__)
        self.local_mode = local_mode
        self.twitters: dict[int, Twitter] = {}

    async def _get_twitter(self, user_id: int) -> Twitter:
        if user_id not in self.twitters:
            self.twitters[user_id] = Twitter(self.local_mode)

        return self.twitters[user_id]

    async def _get_logged_in_twitter(self, user: User) -> Twitter:
        if not user.twitter_username or not user.twitter_email or not user.twitter_password:
            raise ValueError("User has no twitter credentials")

        twitter = await self._get_twitter(user.telegram_id)
        if not twitter.logged_in:
            await twitter.login_as_user(user)

        return twitter

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        self.logger.info("User %s started the conversation.", update.effective_user.first_name)
        await update.message.reply_text(text="I'm a bot, please talk to me!")

    async def login(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return

        user = User.get_or_none(User.telegram_id == update.effective_user.id)
        if user is None:
            await update.message.reply_text("You are not registered.")
            return

        await self._get_logged_in_twitter(user)
        await update.message.reply_text("Logged in as user")

    async def post_init(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Started")
        await application.bot.set_my_commands(
            [
                ("login", "Login into twitter"),
            ]
        )

    async def post_stop(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Stopped")

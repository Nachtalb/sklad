import logging
import re

from telegram import InputMediaPhoto, InputMediaVideo, Message, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes
from yarl import URL

from sklad.db import Tweet, User
from sklad.twitter import Twitter, TwitterMedia


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

    async def send_single_tweet_attachment(
        self, attachment: TwitterMedia, message: Message, caption: str | None = None
    ) -> Message:
        if attachment["type"] == "photo":
            return await message.reply_photo(photo=attachment["url"], caption=caption, parse_mode=ParseMode.HTML)
        elif attachment["type"] == "video":
            return await message.reply_video(
                video=attachment["url"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                thumbnail=attachment["thumbnail_url"],
                width=attachment["width"],
                height=attachment["height"],
            )
        elif attachment["type"] == "gif":
            return await message.reply_animation(
                animation=attachment["url"], caption=caption, parse_mode=ParseMode.HTML
            )
        else:
            raise ValueError(f"Unknown attachment type: {attachment['type']}")

    async def send_multi_tweet_attachments(
        self, attachments: list[TwitterMedia], message: Message, caption: str | None = None
    ) -> tuple[Message, ...]:
        input_media: list[InputMediaVideo | InputMediaPhoto] = []
        for media in attachments:
            if media["type"] == "photo":
                input_media.append(InputMediaPhoto(media["url"], caption=caption, parse_mode=ParseMode.HTML))
            elif media["type"] in ("video", "gif"):
                input_media.append(
                    InputMediaVideo(
                        media["url"],
                        thumbnail=media["thumbnail_url"],
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        width=media["width"],
                        height=media["height"],
                    )
                )
            else:
                self.logger.warning("Unknown attachment type: %s", media["type"])
                continue
            caption = None

        return await message.reply_media_group(media=input_media)

    async def send_tweet(self, tweet: Tweet, message: Message) -> tuple[Message, ...]:
        caption = f'{tweet.text}\n\n<a href="{tweet.url}">View on Twitter</a> | <a href="{tweet.user_url}">{tweet.user_name}</a>'
        caption = self._replace_mentions(caption)

        attachments: list[TwitterMedia] = tweet.attachments

        if not attachments:
            return (await message.reply_text(caption, parse_mode=ParseMode.HTML),)
        elif len(attachments) == 1:
            return (await self.send_single_tweet_attachment(attachments[0], message, caption),)
        else:
            return await self.send_multi_tweet_attachments(attachments, message, caption)

    def _check_logged_in(self, user_id: int) -> bool:
        return user_id in self.twitters and self.twitters[user_id].logged_in

    def _replace_mentions(self, text: str) -> str:
        return re.sub(r"@(\w+)", r'<a href="https://twitter.com/\1">@\1</a>', text)

    async def send_tweet_by_id_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return

        if not context.args:
            await update.message.reply_text("Please provide a tweet id or url")
            return

        tweet_id_url = context.args[0]

        if not tweet_id_url.isdigit():
            try:
                tweet_id = URL(tweet_id_url).name
            except ValueError:
                await update.message.reply_text("Invalid tweet url")
                return
        else:
            tweet_id = tweet_id_url

        if not self._check_logged_in(update.effective_user.id):
            await update.message.reply_text("You are not logged in.")
            return

        twitter = await self._get_twitter(update.effective_user.id)
        tweet = await twitter.get_tweet_by_id(tweet_id)

        if tweet is None:
            await update.message.reply_text("Tweet not found")
            return

        await self.send_tweet(tweet, update.message)

    async def post_init(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Started")
        await application.bot.set_my_commands(
            [
                ("login", "Login into twitter"),
                ("tweet", "Send a tweet by id or url"),
            ]
        )

    async def post_stop(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Stopped")

import logging
import re
from asyncio import as_completed
from typing import Any, cast

from telegram import Bot as TelegramBot
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, Message, Update
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
        if (not user.twitter_username or not user.twitter_email or not user.twitter_password) or (
            not user.twitter_cookies
        ):
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
                input_media.append(InputMediaPhoto(media["url"]))
            elif media["type"] in ("video", "gif"):
                input_media.append(
                    InputMediaVideo(
                        media["url"],
                        thumbnail=media["thumbnail_url"],
                        width=media["width"],
                        height=media["height"],
                    )
                )
            else:
                self.logger.warning("Unknown attachment type: %s", media["type"])
                continue

        return await message.reply_media_group(media=input_media, caption=caption, parse_mode=ParseMode.HTML)

    def _get_tweet_caption(self, tweet: Tweet) -> str:
        caption = f'{tweet.text}\n\n<a href="{tweet.url}">View on Twitter</a> | <a href="{tweet.user_url}">{tweet.user_name}</a>'
        caption = self._replace_mentions(caption)
        return caption

    async def send_tweet(self, tweet: Tweet, message: Message, no_caption: bool = False) -> tuple[Message, ...]:
        caption = None if no_caption else self._get_tweet_caption(tweet)
        attachments: list[TwitterMedia] = tweet.attachments

        if caption is None and not attachments:
            raise ValueError("No caption or attachments found")

        if not attachments:
            return (await message.reply_text(caption, parse_mode=ParseMode.HTML),)  # type: ignore[arg-type]
        elif len(attachments) == 1:
            return (await self.send_single_tweet_attachment(attachments[0], message, caption),)
        else:
            return await self.send_multi_tweet_attachments(attachments, message, caption)

    def _check_logged_in(self, user_id: int) -> bool:
        return user_id in self.twitters and self.twitters[user_id].logged_in

    def _replace_mentions(self, text: str) -> str:
        return re.sub(r"@(\w+)", r'<a href="https://twitter.com/\1">@\1</a>', text)

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.callback_query or not update.callback_query.data:
            return

        query = update.callback_query

        try:
            data = cast(dict[str, Any], query.data)
            if "action" not in data:
                raise TypeError("No action found in data")
        except TypeError:
            await query.answer()
            await query.message.delete()  # type: ignore[union-attr]
            return

        match data["action"]:
            case "next_tweet":
                await query.answer("Not implemented")
            case "previous_tweet":
                await query.answer("Not implemented")
            case "send_to_verus:":
                await query.answer("Not implemented")
            case "skip_tweet":
                await query.answer("Not implemented")
            case _:
                self.logger.warning("Unknown action: %s", data["action"])
                await query.answer("Unknown action")

    def _buttons_to_markup(self, buttons: list[dict[str, Any]], cols: int = 2) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(**button) for button in buttons[i : i + cols]] for i in range(0, len(buttons), cols)]
        )

    async def send_tweet_manage_buttons(
        self, bot: TelegramBot, user_id: int, tweet: Tweet, data: dict[str, Any]
    ) -> None:
        if user_id not in self.twitters:
            return

        new_data = {
            "tweet_id": tweet.id,
            "user_id": user_id,
        }

        match data["action"]:
            case "setup":
                new_data["message_ids"] = data["message_ids"]
            case _:
                return

        buttons = [
            {"text": "Next Tweet", "callback_data": {"action": "next_tweet", **new_data}},
            {"text": "Previous Tweet", "callback_data": {"action": "previous_tweet", **new_data}},
            {"text": "Send to Verus", "callback_data": {"action": "send_to_verus", **new_data}},
            {"text": "Skip Tweet", "callback_data": {"action": "skip_tweet", **new_data}},
        ]

        await bot.send_message(
            user_id,
            self._get_tweet_caption(tweet),
            reply_markup=self._buttons_to_markup(buttons),
            parse_mode=ParseMode.HTML,
        )

    async def send_latest_tweet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return

        tweets = Tweet.select().where(Tweet.processed == False).order_by(Tweet.created_at.desc()).limit(1)  # noqa: E712
        if not tweets:
            await update.message.reply_text("No tweets found")
            return

        messages = await self.send_tweet(tweets[0], update.message, no_caption=True)

        if not messages:
            await update.message.reply_text("No tweets found")
            return

        await self.send_tweet_manage_buttons(
            context.bot,
            update.effective_user.id,
            tweets[0],
            {"action": "setup", "message_ids": [m.message_id for m in messages]},
        )

    async def get_timeline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return

        if not self._check_logged_in(update.effective_user.id):
            await update.message.reply_text("You are not logged in.")
            return

        progress = await update.message.reply_text("Getting timeline...")
        twitter = await self._get_twitter(update.effective_user.id)
        tweets = await twitter.get_timeline()
        await progress.delete()

        if not tweets:
            await update.message.reply_text("No tweets found")
            return
        await self.send_latest_tweet(update, context)

    async def send_tweet_by_id_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return

        if not self._check_logged_in(update.effective_user.id):
            await update.message.reply_text("You are not logged in.")
            return

        text = set(
            context.args
            if context.args
            else update.message.text.replace("\n", " ").split(" ") if update.message.text else []
        )
        if not text:
            await update.message.reply_text("Please provide a tweet id or url")
            return

        ids = set(filter(None, [self._get_tweet_id_from_text(t) for t in text]))
        if not any(ids):
            await update.message.reply_text("Invalid tweet id or url")
            return
        elif len(ids) != len(text):
            await update.message.reply_text("Not all tweet ids or urls are valid or duplicates are present")

        twitter = await self._get_twitter(update.effective_user.id)

        not_found = sent = False
        for future in as_completed([twitter.get_tweet_by_id(tweet_id) for tweet_id in ids]):
            tweet = await future
            if tweet is None:
                not_found = True
                continue
            sent = True
            await self.send_tweet(tweet, update.message)

        if not sent:
            await update.message.reply_text("No tweets found")
        elif not_found:
            await update.message.reply_text("Some tweets were not found")

    def _get_tweet_id_from_text(self, text: str) -> str | None:
        text = text.strip()
        if text.isdigit():
            return text

        try:
            url = URL(text)
            if url.name.isdigit():
                return url.name
        except ValueError:
            pass
        return None

    async def auto_login(self) -> None:
        for user in User.select().where(User.twitter_cookies.is_null(False)):
            await self._get_logged_in_twitter(user)

    async def post_init(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Started")
        await application.bot.set_my_commands(
            [
                ("login", "Login into twitter"),
                ("tweet", "Send a tweet by id or url"),
                ("timeline", "Get your timeline"),
            ]
        )

        await self.auto_login()

    async def post_stop(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Stopped")

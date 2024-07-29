import logging
import re
from asyncio import as_completed
from io import BytesIO
from itertools import chain
from typing import Any, cast

from aiohttp import ClientSession
from telegram import Animation
from telegram import Bot as TelegramBot
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    PhotoSize,
    Update,
    Video,
)
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes
from yarl import URL

from sklad.db import Tweet, User
from sklad.twitter import Twitter, TwitterMedia


class Bot:
    def __init__(self, local_mode: bool = False, admins: list[str] = []) -> None:
        self.logger = logging.getLogger(__name__)
        self.local_mode = local_mode
        self.twitters: dict[int, Twitter] = {}
        self.aio_session: ClientSession = ClientSession()
        self.admins = admins

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

    async def _get_telegram_obj(
        self, attachment: TwitterMedia, bot: TelegramBot, gif_as_video: bool = False
    ) -> PhotoSize | Video | Animation | None:
        if not attachment.get("telegram_data"):
            return None

        if attachment["type"] == "photo":
            return PhotoSize.de_json(attachment["telegram_data"], bot)
        elif attachment["type"] == "video" or (attachment["type"] == "gif" and gif_as_video):
            return Video.de_json(attachment["telegram_data"], bot)
        elif attachment["type"] == "gif":
            return Animation.de_json(attachment["telegram_data"], bot)
        return None

    async def _download_to_buffer(self, url: str) -> BytesIO:
        self.logger.info("Downloading %s", url)
        async with self.aio_session.get(url) as response:
            return BytesIO(await response.read())

    async def send_single_tweet_attachment(self, tweet: Tweet, message: Message, caption: str | None = None) -> Message:
        attachment = tweet.attachments[0]
        telegram_obj = await self._get_telegram_obj(attachment, message.get_bot())
        filename = URL(attachment["url"]).name

        if attachment["type"] == "photo":
            telegram_obj = cast(PhotoSize | None, telegram_obj)
            message = await message.reply_photo(
                photo=telegram_obj or attachment["url"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                filename=filename,
            )
            if not telegram_obj:
                attachment["telegram_data"] = message.photo[0].to_dict()
        elif attachment["type"] == "video":
            telegram_obj = cast(Video | None, telegram_obj)
            attachment_obj = None
            thumbnail_obj = None
            if not telegram_obj and attachment["size"] / 1_000_000 > 50:
                self.logger.info("Video size: %s MB, this might take a while", attachment["size"] / 1_000_000)
                await message.reply_text(
                    f"Video size: `{attachment['size'] / 1_000_000:.2f} MB`, this might take a while",
                    parse_mode=ParseMode.MARKDOWN,
                )
                attachment_obj = await self._download_to_buffer(attachment["url"])
                thumbnail_obj = await self._download_to_buffer(attachment["thumbnail_url"])

            message = await message.reply_video(
                video=telegram_obj or attachment_obj or attachment["url"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                thumbnail=(thumbnail_obj or attachment["thumbnail_url"]) if not telegram_obj else None,
                width=attachment["width"] if not telegram_obj else None,
                height=attachment["height"] if not telegram_obj else None,
                filename=filename,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=60,
                duration=attachment["duration"] if not telegram_obj else None,
            )
            if not telegram_obj:
                attachment["telegram_data"] = (message.video or message.animation).to_dict()  # type: ignore[union-attr]
        elif attachment["type"] == "gif":
            telegram_obj = cast(Animation | None, telegram_obj)
            message = await message.reply_animation(
                animation=telegram_obj or attachment["url"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                filename=filename,
            )
            if not telegram_obj:
                attachment["telegram_data"] = message.animation.to_dict()  # type: ignore[union-attr]
        else:
            raise ValueError(f"Unknown attachment type: {attachment['type']}")
        tweet.save()
        return message

    async def send_multi_tweet_attachments(
        self, tweet: Tweet, message: Message, caption: str | None = None
    ) -> tuple[Message, ...]:
        attachments: list[TwitterMedia] = tweet.attachments
        input_media: list[InputMediaVideo | InputMediaPhoto] = []
        for media in attachments:
            telegram_obj = await self._get_telegram_obj(media, message.get_bot(), gif_as_video=True)
            if media["type"] == "photo":
                telegram_obj = cast(PhotoSize | None, telegram_obj)
                input_media.append(InputMediaPhoto(telegram_obj or media["url"]))
            elif media["type"] in ("video", "gif"):
                telegram_obj = cast(Video | None, telegram_obj)
                input_media.append(
                    InputMediaVideo(
                        telegram_obj or media["url"],
                        thumbnail=media["thumbnail_url"] if not telegram_obj else None,
                        width=media["width"] if not telegram_obj else None,
                        height=media["height"] if not telegram_obj else None,
                    )
                )
            else:
                self.logger.warning("Unknown attachment type: %s", media["type"])
                continue

        messages = await message.reply_media_group(media=input_media, caption=caption, parse_mode=ParseMode.HTML)
        for attachment, message in zip(attachments, messages):
            if not attachment.get("telegram_data"):
                if attachment["type"] == "photo":
                    attachment["telegram_data"] = message.photo[0].to_dict()
                elif attachment["type"] in ("video", "gif"):
                    attachment["telegram_data"] = (message.video or message.animation).to_dict()  # type: ignore[union-attr]
        tweet.save()
        return messages

    def _get_tweet_caption(self, tweet: Tweet) -> str:
        processed = " | Processed" if tweet.processed else ""
        caption = f'{tweet.text}\n\n<a href="{tweet.url}">View on Twitter</a> | <a href="{tweet.user_url}">{tweet.user_name}</a>{processed}'
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
            return (await self.send_single_tweet_attachment(tweet, message, caption),)
        else:
            return await self.send_multi_tweet_attachments(tweet, message, caption)

    def _check_logged_in(self, user_id: int) -> bool:
        return user_id in self.twitters and self.twitters[user_id].logged_in

    def _replace_mentions(self, text: str) -> str:
        return re.sub(r"@(\w+)", r'<a href="https://twitter.com/\1">@\1</a>', text)

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (
            not update.effective_user
            or not update.callback_query
            or not update.callback_query.data
            or not update.callback_query.message
        ):
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

        if action := getattr(self, f"_button_{data['action']}", None):
            self.logger.info("Button: Action: %s", data["action"])
            await query.answer()
            await action(query.message, data)
        else:
            self.logger.warning("Button: Unknown action: %s", data["action"])
            await query.answer("Unknown action")

            if data.get("delete_if_unknown", False):
                try:
                    await query.message.delete()  # type: ignore[union-attr]
                except Exception as e:
                    self.logger.error("Error deleting message: %s", e)

    def _buttons_to_markup(self, buttons: list[dict[str, Any]], cols: int = 2) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(**button) for button in buttons[i : i + cols]] for i in range(0, len(buttons), cols)]
        )

    async def _button_next_tweet(self, message: Message, data: dict[str, Any]) -> None:
        current_tweet = Tweet.get_or_none(Tweet.id == data["tweet_id"])
        next_tweet = (
            Tweet.select()
            .where((Tweet.created_at < current_tweet.created_at) & (Tweet.processed == False))  # noqa: E712
            .order_by(Tweet.created_at.desc())
            .first()
        )
        await self._new_timeline_tweet(message, data, next_tweet)

    async def _button_previous_tweet(self, message: Message, data: dict[str, Any]) -> None:
        current_tweet = Tweet.get_or_none(Tweet.id == data["tweet_id"])
        previous_tweet = (
            Tweet.select()
            .where((Tweet.created_at > current_tweet.created_at) & (Tweet.processed == False))  # noqa: E712
            .order_by(Tweet.created_at.asc())
            .first()
        )
        await self._new_timeline_tweet(message, data, previous_tweet)

    async def _button_reset_progress(self, message: Message, data: dict[str, Any]) -> None:
        tweet = Tweet.get_or_none(Tweet.id == data["tweet_id"])
        if not tweet:
            return

        processed = Tweet.update(processed=False).where(Tweet.processed == True)  # noqa: E712
        processed.execute()

        await self._new_timeline_tweet(message, data, tweet)

    async def _button_send_to_verus(self, message: Message, data: dict[str, Any]) -> None:
        tweet = Tweet.get_or_none(Tweet.id == data["tweet_id"])
        if not tweet:
            return

        tweet.processed = True
        tweet.save()

        await self._button_next_tweet(message, data)

    async def _button_to_latest(self, message: Message, data: dict[str, Any]) -> None:
        tweet = Tweet.select().where(Tweet.processed == False).order_by(Tweet.created_at.desc()).first()  # noqa: E712
        if not tweet:
            await message.edit_text("No more tweets found")
            return
        await self._new_timeline_tweet(message, data, tweet)

    async def _new_timeline_tweet(self, message: Message, data: dict[str, Any], tweet: Tweet) -> None:
        if "message_ids" in data:
            await message.get_bot().delete_messages(message.chat_id, data["message_ids"])

        if tweet:
            messages = await self.send_tweet(tweet, message, no_caption=True)
            data["message_ids"] = [m.message_id for m in messages]

            await self.send_tweet_manage_buttons(
                message,
                tweet,
                data,
                previous_msg_id=message.message_id,
            )
        else:
            await message.edit_text("No more tweets found")

    async def send_tweet_manage_buttons(
        self,
        message: Message,
        tweet: Tweet,
        data: dict[str, Any],
        previous_msg_id: int | None = None,
    ) -> None:
        data.update(
            {
                "tweet_id": tweet.id,
            }
        )

        data.pop("action", None)

        buttons = [
            {"text": "Next Tweet", "callback_data": {"action": "next_tweet", **data}},
            {"text": "Previous Tweet", "callback_data": {"action": "previous_tweet", **data}},
            {"text": "Send to Verus", "callback_data": {"action": "send_to_verus", **data}},
            {"text": "Reset Progress", "callback_data": {"action": "reset_progress", **data}},
            {"text": "To Latest", "callback_data": {"action": "to_latest", **data}},
        ]

        if previous_msg_id:
            await message.edit_text(
                text=self._get_tweet_caption(tweet),
                parse_mode=ParseMode.HTML,
                reply_markup=self._buttons_to_markup(buttons),
            )
        else:
            await message.reply_text(
                self._get_tweet_caption(tweet),
                reply_markup=self._buttons_to_markup(buttons),
                parse_mode=ParseMode.HTML,
            )

    async def send_latest_tweet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return

        tweets = Tweet.select().where(Tweet.processed == False).order_by(Tweet.created_at.desc()).limit(1)  # noqa: E712
        #  tweets = Tweet.select().order_by(Tweet.created_at.desc()).limit(1)  # noqa: E712
        if not tweets:
            await update.message.reply_text("No tweets found")
            return

        messages = await self.send_tweet(tweets[0], update.message, no_caption=True)

        if not messages:
            await update.message.reply_text("No tweets found")
            return

        await self.send_tweet_manage_buttons(
            update.message,
            tweets[0],
            {"message_ids": [m.message_id for m in messages]},
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

        if not update.message.text and not context.args:
            await update.message.reply_text("Please provide a tweet id or url")
            return

        if context.args:
            text = set(context.args)
        else:
            text = set(update.message.text.replace("\n", " ").split(" "))  # type: ignore[union-attr]

        ids = set(map(int, chain.from_iterable(self._get_tweet_id_from_text(tweet_id) for tweet_id in text)))
        if not any(ids):
            await update.message.reply_text("Invalid tweet id or url")
            return

        twitter = await self._get_twitter(update.effective_user.id)

        found = set()
        for future in as_completed([twitter.get_tweet_by_id(tweet_id) for tweet_id in ids]):
            tweet = await future
            if tweet is None:
                continue
            found.add(tweet.tweet_id)
            await self.send_tweet(tweet, update.message)

        if not found:
            await update.message.reply_text("No tweets found")
            return
        elif found != ids:
            not_found = map(str, ids - found)
            base = "https://x.com/i/web/status/"
            not_found_str = base + f"\n{base}".join(not_found)

            await update.message.reply_text(f"Some tweets were not found:\n{not_found_str}")

    def _get_tweet_id_from_text(self, text: str) -> list[str]:
        text = text.strip()
        if text.isdigit():
            return [text]

        matches = re.findall(r"status/(\d+)", text)
        return matches

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

        for admin in self.admins:
            await application.bot.send_message(admin, "Sklad Started")

    async def post_stop(self, application: Application) -> None:  # type: ignore[type-arg]
        self.logger.info("Sklad Stopped")

        for admin in self.admins:
            await application.bot.send_message(admin, "Sklad Stopped")

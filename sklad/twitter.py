import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, TypedDict

from aiohttp import ClientSession
from twikit.errors import TweetNotAvailable
from twikit.twikit_async.client import Client
from twikit.twikit_async.tweet import Tweet as TwiTweet
from yarl import URL

from sklad.db import DATABASE, Tweet, User

TwitterMedia = TypedDict(
    "TwitterMedia",
    {
        "type": str,
        "url": str,
        "width": int,
        "height": int,
        "thumbnail_url": str,
        "telegram_data": dict[str, Any],
        "size": int,
        "duration": int | None,
    },
)


class Twitter:
    def __init__(self, local_mode: bool = False) -> None:
        self.client = Client("en-us")
        self.local_mode = local_mode
        self.logged_in = False
        self.aio_session: ClientSession = ClientSession()
        self.logger = logging.getLogger("Twitter [-]")

    async def login_as_user(self, user: User) -> None:
        if user.twitter_cookies:
            self.logger.info("Logging in with cookies")
            self.client.set_cookies(user.twitter_cookies)
            self.logged_in = True
        else:
            self.logger.info("Logging in with credentials")
            await self.login(username=user.twitter_username, email=user.twitter_email, password=user.twitter_password)
            user.twitter_cookies = self.client.get_cookies()
            user.save()

    async def login(self, username: str, email: str, password: str) -> None:
        await self.client.login(auth_info_1=username, auth_info_2=email, password=password)
        self.logged_in = True
        self.logger.name = f"Twitter [{username}]"

    def _custom_img_url(self, url: str | URL, size: str = "medium", format: str = "jpg") -> URL:
        url = URL(url)
        url.with_query({"name": size, "format": format})
        url.with_name(Path(url.name).stem + "." + format)
        return url

    async def _head_request(self, url: str) -> dict[str, Any]:
        async with self.aio_session.head(url) as response:
            return response.headers  # type: ignore[return-value]

    async def _get_media_size(self, url: str) -> int:
        headers = await self._head_request(url)
        return int(headers["Content-Length"])

    async def _get_relevant_media_info(self, attachment: dict[str, Any]) -> TwitterMedia | None:
        if attachment["type"] == "photo":
            dimensions = attachment["sizes"]["medium"]
            url = str(self._custom_img_url(attachment["media_url_https"], size="medium", format="jpg"))
            size = await self._get_media_size(url)

            return TwitterMedia(
                type="photo",
                url=str(url),
                width=dimensions["w"],
                height=dimensions["h"],
                thumbnail_url=str(url),
                telegram_data={},
                size=size,
                duration=None,
            )
        elif attachment["type"] == "video":
            url = attachment["video_info"]["variants"][-1]["url"]
            size = await self._get_media_size(url)
            width, height = (int(s) for s in url.split("/")[-2].split("x"))
            thumbnail_url = attachment["media_url_https"]
            duration = attachment["video_info"]["duration_millis"]

            return TwitterMedia(
                type="video",
                url=url,
                width=width,
                height=height,
                thumbnail_url=thumbnail_url,
                telegram_data={},
                size=size,
                duration=int(duration / 1000),
            )
        elif attachment["type"] == "animated_gif":
            url = attachment["video_info"]["variants"][-1]["url"]
            dimensions = attachment["original_info"]
            thumbnail_url = ""
            size = await self._get_media_size(url)

            return TwitterMedia(
                type="gif",
                url=url,
                width=dimensions["width"],
                height=dimensions["height"],
                thumbnail_url=thumbnail_url,
                telegram_data={},
                size=size,
                duration=None,
            )
        else:
            self.logger.warning("Unknown attachment type: %s", attachment["type"])
        return None

    async def get_relevant_media_info(self, data: list[dict[str, Any]]) -> list[TwitterMedia]:
        attachments = list(
            filter(None, await asyncio.gather(*[self._get_relevant_media_info(attachment) for attachment in data]))
        )

        return attachments

    async def download_to_buffer(self, url: str) -> BytesIO:
        self.logger.info("Downloading %s", url)
        async with self.aio_session.get(url) as response:
            return BytesIO(await response.read())

    async def get_tweet_by_id(self, tweet_id: str | int) -> Tweet | None:
        if int(tweet_id) > 2**63:
            # Tweet ID is too large to be an SQL integer. Twitter IDs are 64-bit integers.
            return None
        if tweet := Tweet.get_or_none(Tweet.tweet_id == tweet_id):
            return tweet  # type: ignore[no-any-return]

        try:
            tweet = await self.client.get_tweet_by_id(str(tweet_id))
        except TweetNotAvailable:
            return None
        return await self._process_tweet(tweet)

    async def _process_tweet(self, tweet: TwiTweet) -> Tweet:
        tweet_object = Tweet.get_or_none(Tweet.tweet_id == tweet.id)
        if tweet_object is None:
            tweet_object = Tweet(
                tweet_id=tweet.id,
                text=tweet.text,
                created_at=tweet.created_at,
                user_id=tweet.user.id,
                user_name=tweet.user.name,
                user_screen_name=tweet.user.screen_name,
                attachments=await self.get_relevant_media_info(tweet.media),
            )
            tweet_object.save()
        return tweet_object  # type: ignore[no-any-return]

    async def get_timeline(self) -> list[Tweet]:
        self.logger.info("Getting latest timeline")
        tweets = await self.client.get_latest_timeline()

        with DATABASE.atomic():
            tweet_objects = await asyncio.gather(*[self._process_tweet(tweet) for tweet in tweets])

        return tweet_objects

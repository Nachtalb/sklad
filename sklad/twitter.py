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

TwitterMedia = TypedDict("TwitterMedia", {"type": str, "url": str, "width": int, "height": int, "thumbnail_url": str})


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

    def get_relevant_media_info(self, data: list[dict[str, Any]]) -> list[TwitterMedia]:
        attachments = []
        for attachment in data:
            if attachment["type"] == "photo":
                size = attachment["sizes"]["medium"]
                url = str(self._custom_img_url(attachment["media_url_https"], size="medium", format="jpg"))
                attachments.append(
                    TwitterMedia(
                        type="photo",
                        url=str(url),
                        width=size["w"],
                        height=size["h"],
                        thumbnail_url=str(url),
                    )
                )
            elif attachment["type"] == "video":
                url = attachment["video_info"]["variants"][-1]["url"]
                width, height = (int(s) for s in url.split("/")[-2].split("x"))
                thumbnail_url = attachment["media_url_https"]
                attachments.append(
                    TwitterMedia(
                        type="video",
                        url=url,
                        width=width,
                        height=height,
                        thumbnail_url=thumbnail_url,
                    )
                )
            elif attachment["type"] == "animated_gif":
                url = attachment["video_info"]["variants"][-1]["url"]
                size = attachment["original_info"]
                thumbnail_url = ""
                attachments.append(
                    TwitterMedia(
                        type="gif",
                        url=url,
                        width=size["width"],
                        height=size["height"],
                        thumbnail_url=thumbnail_url,
                    )
                )
            else:
                self.logger.warning("Unknown attachment type: %s", attachment["type"])
                continue

        return attachments

    async def download_to_buffer(self, url: str) -> BytesIO:
        self.logger.info("Downloading %s", url)
        async with self.aio_session.get(url) as response:
            return BytesIO(await response.read())

    async def get_tweet_by_id(self, tweet_id: str | int) -> Tweet | None:
        if tweet := Tweet.get_or_none(Tweet.tweet_id == tweet_id):
            return tweet  # type: ignore[no-any-return]

        try:
            tweet = await self.client.get_tweet_by_id(str(tweet_id))
        except TweetNotAvailable:
            return None
        return self._process_tweet(tweet)

    def _process_tweet(self, tweet: TwiTweet) -> Tweet:
        tweet_object = Tweet.get_or_none(Tweet.tweet_id == tweet.id)
        if tweet_object is None:
            tweet_object = Tweet(
                tweet_id=tweet.id,
                text=tweet.text,
                created_at=tweet.created_at,
                user_id=tweet.user.id,
                user_name=tweet.user.name,
                user_screen_name=tweet.user.screen_name,
                attachments=self.get_relevant_media_info(tweet.media),
            )
            tweet_object.save()
        return tweet_object  # type: ignore[no-any-return]

    async def get_timeline(self) -> list[Tweet]:
        self.logger.info("Getting latest timeline")
        tweets = await self.client.get_latest_timeline()

        with DATABASE.atomic():
            tweet_objects = [self._process_tweet(tweet) for tweet in tweets]

        return tweet_objects

from twikit.twikit_async.client import Client

from sklad.db import DATABASE, Tweet, User


class Twitter:
    def __init__(self, local_mode: bool = False) -> None:
        self.client = Client()
        self.local_mode = local_mode
        self.logged_in = False

    async def login_as_user(self, user: User) -> None:
        await self.login(username=user.twitter_username, email=user.twitter_email, password=user.twitter_password)

    async def login(self, username: str, email: str, password: str) -> None:
        await self.client.login(auth_info_1=username, auth_info_2=email, password=password)
        self.logged_in = True

    async def get_timeline(self) -> list[Tweet]:
        tweets = await self.client.get_latest_timeline()

        tweet_objects = []
        with DATABASE.atomic():
            for tweet in tweets:
                tweet_object = Tweet.get_or_none(Tweet.tweet_id == tweet.id)
                if tweet_object is None:
                    tweet_objects.append(
                        Tweet(
                            tweet_id=tweet.id,
                            text=tweet.text,
                            created_at=tweet.created_at,
                            user_id=tweet.user.id,
                            user_name=tweet.user.name,
                            user_screen_name=tweet.user.screen_name,
                            main_attachment=tweet.media,
                        )
                    )

        return tweet_objects

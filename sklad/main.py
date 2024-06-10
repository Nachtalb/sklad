import argparse
import logging
from functools import reduce

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)

from .bot import Bot

TG_BASE_URL = "https://api.telegram.org/bot"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    # Typical local path: "http://localhost:8081/bot"
    parser.add_argument("--base-url", default=TG_BASE_URL, help="Base URL for the bot API")
    parser.add_argument("--local-mode", action="store_true", help="Run the bot in local mode", default=False)
    parser.add_argument("--admins", required=True, type=str, help="List of admin ids, separated by commas.")

    sub_parsers = parser.add_subparsers()
    webhook_parser = sub_parsers.add_parser("webhook")
    webhook_parser.add_argument("--webhook-url", required=True)
    webhook_parser.add_argument("--webhook-path", default="")
    webhook_parser.add_argument("--listen", default="0.0.0.0")
    webhook_parser.add_argument("--port", type=int, default=8433)

    webhook_parser.set_defaults(webhook=True)

    args = parser.parse_args()

    bot = Bot(local_mode=args.local_mode)

    persistence = PicklePersistence(filepath="sklad_bot.dat")
    app = (
        ApplicationBuilder()
        .token(args.token)
        .persistence(persistence)
        .arbitrary_callback_data(True)
        .post_init(bot.post_init)
        .post_stop(bot.post_stop)
        .base_url(args.base_url)
        .local_mode(args.local_mode)
        .build()
    )

    users = [filters.User(int(user)) for user in args.admins.split(",")]
    user_filter = users[0]
    if len(users) > 1:
        user_filter = reduce(lambda x, y: x | y, users)  # type: ignore[return-value, arg-type]

    default_filter = filters.ChatType.PRIVATE & user_filter

    app.add_handler(CommandHandler("start", bot.start, filters=default_filter))
    app.add_handler(CommandHandler("login", bot.login, filters=default_filter))
    app.add_handler(CommandHandler("tweet", bot.send_tweet_by_id_url, filters=default_filter))
    app.add_handler(CommandHandler("timeline", bot.get_timeline, filters=default_filter))
    app.add_handler(MessageHandler(default_filter & filters.TEXT, bot.send_tweet_by_id_url))
    app.add_handler(CallbackQueryHandler(bot.callback_query_handler))

    if hasattr(args, "webhook"):
        app.run_webhook(
            listen=args.listen,
            port=args.port,
            webhook_url=args.webhook_url,
            url_path=args.webhook_path,
            secret_token="ASecretTokenIHaveChangedByNowOrNot",
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting...")

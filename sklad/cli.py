import argparse
import logging

from tabulate import tabulate

from sklad.db import User, setup_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

logger = logging.getLogger("sklad")


def add_user(args: argparse.Namespace) -> None:
    logger.info(f"Adding user {args.username} with telegram id {args.telegram_id}")
    setup_db()
    User.create(username=args.username, telegram_id=args.telegram_id).save()


def del_user(args: argparse.Namespace) -> None:
    logger.info(f"Deleting user {args.username}")
    setup_db()
    user = User.get(User.username == args.username)
    user.delete_instance()


def list_users(args: argparse.Namespace) -> None:
    logger.info("Listing users")
    setup_db()
    users = User.select()
    data = [(user.id, user.username, user.telegram_id) for user in users]
    print(tabulate(data, headers=["ID", "Username", "Telegram ID"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub_parsers = parser.add_subparsers()

    user_parser = sub_parsers.add_parser("user")
    user_parser.set_defaults(func=lambda _: user_parser.print_help())
    sub_user_parsers = user_parser.add_subparsers()

    user_add_parser = sub_user_parsers.add_parser("add")
    user_add_parser.add_argument("username")
    user_add_parser.add_argument("telegram_id")
    user_add_parser.set_defaults(func=add_user)

    user_list_parser = sub_user_parsers.add_parser("list")
    user_list_parser.set_defaults(func=list_users)

    user_delete_parser = sub_user_parsers.add_parser("delete")
    user_delete_parser.add_argument("username")
    user_delete_parser.set_defaults(func=del_user)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Exiting...")

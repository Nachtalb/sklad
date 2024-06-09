import json
from datetime import datetime
from typing import Any

from peewee import BooleanField, CharField, DateTimeField, IntegerField, Model, SqliteDatabase
from playhouse.shortcuts import dict_to_model, model_to_dict
from playhouse.sqlite_ext import JSONField

DATABASE: SqliteDatabase = SqliteDatabase("sklad.db")


class BaseModel(Model):  # type: ignore[misc]
    class Meta:
        database = DATABASE

    def to_dict(self, exceptions: list[str] = []) -> dict[str, Any]:
        data = model_to_dict(self, backrefs=True, recurse=True)
        for key in exceptions:
            data.pop(key, None)
        return data  # type: ignore[no-any-return]

    def to_json(self, exceptions: list[str] = []) -> str:
        data = self.to_dict(exceptions=exceptions)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return json.dumps(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], force_insert: bool = False) -> "BaseModel":
        instance = dict_to_model(cls, data)
        instance.save(force_insert=force_insert)
        return instance  # type: ignore[no-any-return]

    @classmethod
    def from_json(cls, data: str, force_insert: bool = False) -> "BaseModel":
        json_data = json.loads(data)
        for key, value in json_data.items():
            if value is not None and cls._meta.fields.get(key) == DateTimeField:
                json_data[key] = datetime.fromisoformat(value)
        return cls.from_dict(json_data, force_insert=force_insert)


class Tweet(BaseModel):
    tweet_id = IntegerField(unique=True)
    text = CharField()
    created_at = DateTimeField()
    user_id = IntegerField()
    user_name = CharField()
    user_screen_name = CharField()
    main_attachment = JSONField(null=True)

    processed = BooleanField(default=False)
    processed_at = DateTimeField(null=True)


class User(BaseModel):
    username = CharField(unique=True)
    role = CharField(default="user")
    telegram_id = IntegerField(null=True)

    def __str__(self) -> str:
        return self.username  # type: ignore[no-any-return]


def setup_db() -> SqliteDatabase:
    global DATABASE
    DATABASE.connect()
    DATABASE.create_tables([User])
    return DATABASE

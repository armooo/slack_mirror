import os
import logging
from datetime import datetime

from sqlalchemy import (
    Column,
    Text,
    UnicodeText,
    DateTime,
)
from sqlalchemy.ext.declarative import declarative_base
from rauth.service import OAuth2Service
import requests

Base = declarative_base()

LOGGER = logging.getLogger(__name__)


def get_service(settings):
    return OAuth2Service(
        name='slack',
        client_id=settings['slack.oauth2_key'],
        client_secret=settings['slack.oauth2_secret'],
        access_token_url='https://slack.com/api/oauth.access',
        authorize_url='https://slack.com/oauth/authorize')


BASE_PATH = os.path.dirname(os.path.abspath(__file__))

BOT_CONFIG_TEMPLATE = """
[program:{USERNAME}]
command = %(here)s/../.env/bin/python %(here)s/../slack_mirror/slack_mirror_script.py %(here)s/../development.ini {USERNAME}
autostart = true
startretries = 15
"""


def add_bot_config(settings, user):
    email = user.email
    template = BOT_CONFIG_TEMPLATE
    config = template.format(USERNAME=email)
    user_config_dir = os.path.join(os.path.abspath(settings['supervisor.config_path']), email)
    os.mkdir(user_config_dir)
    open(os.path.join(user_config_dir, 'bot.conf'), 'w').write(config)


class User(Base):
    __tablename__ = 'site_user'

    id = Column(Text, primary_key=True)
    email = Column(UnicodeText, nullable=False)
    last_log = Column(DateTime, nullable=False, default=datetime.utcnow())
    access_token = Column(Text, nullable=False)
    zulip_key = Column(Text)

    def __init__(self, id_, email, access_token):
        self.id = id_
        self.email = email
        self.access_token = access_token

    @property
    def slack_api(self):
        session = requests.Session()
        session.params = {'token': self.access_token}
        return session

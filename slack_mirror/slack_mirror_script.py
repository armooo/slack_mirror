import argparse
import collections
import json
import logging
import os
import threading

import pyramid.paster
import transaction
import websocket
import zulip as zulip_client

import slack_mirror
from slack_mirror.models import User

LOGGER = logging.getLogger('slack_mirror.slack_mirror_script')


class SlackStateMixin(object):
    def __init__(self):
        self.slack_email_domain = None
        self.slack_users = {}
        self.slack_channels = {}

    def slack_init(self, users, channels, team):
        self.slack_email_domain = team['email_domain']
        self.slack_users = {u['id']: u for u in users}
        self.slack_channels = {c['id']: c for c in channels}

    def slack__email_domain_changed(self, msg):
        self.slack_email_domain = msg['email_domain']

    def slack__team_join(self, msg):
        user = msg['user']
        self.slack_users[user['id']] = user

    def slack__user_change(self, msg):
        user = msg['user']
        self.slack_users[user['id']] = user

    def slack__channel_created(self, msg):
        channel = msg['channel']
        self.slack_channels[channel['id']] = channel

    def slack__channel_rename(self, msg):
        channel = msg['channel']
        self.slack_channels[channel['id']] = channel

    def slack__channel_deleted(self, msg):
        del self.slack_channels[msg['channel']]

    def slack_user_id_to_zulip_user(self, user_id):
        user = self.slack_users[user_id]
        if user['profile']['email']:
            return user['profile']['email']
        else:
            return '{}@{}'.format(user['name'], self.slack_email_domain)

    def slack_channel_id_to_zulip_stream(self, channel_id):
        channel = self.slack_channels[channel_id]
        return '{}/slack'.format(channel['name'])

    def zulip_stream_to_slack_channel_id(self, stream):
        assert stream.endswith('/slack')
        channel_name = stream[:-6]
        for channel in self.slack_channels.values():
            if channel['name'] == channel_name:
                return channel['id']
        return None


class PublicTranslator(SlackStateMixin):
    def __init__(self):
        super(PublicTranslator, self).__init__()
        self.messages_from_zulip = collections.OrderedDict()

    def zulip_init(self):
        # Should join all the slack channels here but bots can't join channels :(
        pass

    def zulip__message(self, msg):
        zulip_msg = msg['message']
        self.messages_from_zulip[(
            zulip_msg['sender_email'],
            zulip_msg['display_recipient'],
            zulip_msg['content'],
        )] = True
        while len(self.messages_from_zulip) > 100:
            self.messages_from_zulip.popitem(last=False)

    def slack__message__channel_join(self, msg):
        pass  # NOOP join spam

    def slack__message(self, msg):
        sender = self.slack_user_id_to_zulip_user(msg['user'])
        recipient = self.slack_channel_id_to_zulip_stream(msg['channel'])
        key = (sender, recipient, msg['text'])

        # This is a sad way to drop messages sent by personal mirrors. This
        # will not work if the message body changes or we see the message from
        # slack before zulip. Using already_sent_mirrored_message_id with a 10
        # sec window on the server size will fix the zulip/slack race but we
        # still have content changes.
        if key in self.messages_from_zulip:
            del self.messages_from_zulip[key]
            return

        zulip_message = dict(
            forged="yes",
            sender=sender,
            type="stream",
            subject='(no topic)',
            to=recipient,
            content=msg['text'],
        )
        self.zulip.send_message(zulip_message)

    def zulip__stream(self, msg):
        if msg['op'] != 'create':
            return
        for stream in msg['streams']:
            if stream['name'].endswith('/slack'):
                self.zulip.join_stream(stream['name'])

    def zulip__subscription(self, msg):
        if msg['op'] != 'add':
            return
        for subscription in msg['subscriptions']:
            name = subscription['name']
            if not name.endswith('/slack'):
                continue
            # Bots can't join channels WTF
            #self.slack.join_channel(name)


class PrivateTranslator(SlackStateMixin):
    def __init__(self, email):
        super(PrivateTranslator, self).__init__()
        self.email = email

    def zulip_init(self):
        for subscription in self.zulip.list_subscriptions():
            if not subscription['name'].endswith('/slack'):
                continue
            # TODO: look at slack subscriptions
            self.slack.join_channel(subscription['name'][:-6])

    def zulip__message(self, msg):
        zulip_msg = msg['message']
        if zulip_msg['sender_email'] != self.email:
            return
        # This is how we prevent loops :(
        if zulip_msg['client'] == 'JabberMirror':
            return
        if not isinstance(zulip_msg['display_recipient'], basestring):
            return
        if not zulip_msg['display_recipient'].endswith('/slack'):
            return

        slack_message = {
            'text': zulip_msg['content'],
            'channel': self.zulip_stream_to_slack_channel_id(zulip_msg['display_recipient']),
            'as_user': True,
        }
        self.slack.send_message(slack_message)

    def zulip__subscription(self, msg):
        if msg['op'] == 'add':
            for subscription in msg['subscriptions']:
                name = subscription['name']
                if not name.endswith('/slack'):
                    continue
                self.slack.join_channel(name[:-6])
        elif msg['op'] == 'remove':
            for subscription in msg['subscriptions']:
                name = subscription['name']
                if not name.endswith('/slack'):
                    continue
                channel_id = self.zulip_stream_to_slack_channel_id(name)
                self.slack.leave_channel(channel_id)


class Zulip(object):
    def __init__(self, translator, zulip_client):
        self.translator = translator
        self.zulip_client = zulip_client

    def process_event(self, event):
        callback_name = 'zulip__' + event['type']
        LOGGER.debug('zulip dispatch: %r', callback_name)
        callback = getattr(self.translator, callback_name, None)
        LOGGER.debug('zulip callback: %r', callback)
        if callback:
            callback(event)

    def run_forever(self):
        self.translator.zulip_init()
        try:
            self.zulip_client.call_on_each_event(self.process_event)
        except:
            LOGGER.exception('zulip run_forever failed')
            os._exit(1)
        LOGGER.error('zulip run_forever stopped')
        os._exit(1)

    def send_message(self, msg):
        LOGGER.debug('Sending message to zulip: %r', msg)
        ret = self.zulip_client.send_message(msg)
        if ret.get("result") != "success":
            LOGGER.error('Failed to send zulip message %r', ret)

    def join_stream(self, stream_name):
        LOGGER.debug('Joining zulip stream %r', stream_name)
        ret = self.zulip_client.add_subscriptions([{'name': stream_name}])
        if ret.get("result") != "success":
            LOGGER.error('Failed to join stream %r', ret)

    def list_subscriptions(self):
        LOGGER.debug('Listing zulip subscriptions')
        ret = self.zulip_client.list_subscriptions()
        if ret.get("result") != "success":
            LOGGER.error('Failed to join stream %r', ret)
        return ret['subscriptions']


class Slack(object):
    def __init__(self, translator, slack_api):
        self.translator = translator
        self.slack_api = slack_api

    def _noop(self, msg):
        pass

    def _dispatch(self, callback_name, msg):
        callback_name = '__'.join(callback_name)
        LOGGER.debug('slack dispatch: %r', callback_name)
        callback = getattr(self.translator, callback_name, None)
        LOGGER.debug('slack callback: %r', callback)
        if callback:
            try:
                callback(msg)
            except:
                LOGGER.exception('Failed to call %r', callback_name)
                raise
            return True
        else:
            return False

    def on_message(self, ws, raw_msg):
        msg = json.loads(raw_msg)

        callback_name = ['slack']
        callback_name.append(msg['type'])
        subtype = msg.get('subtype')
        if subtype:
            callback_name.append(subtype)

        success = self._dispatch(callback_name, msg)
        if not success and subtype:
            callback_name.pop()
            self._dispatch(callback_name, msg)

    def on_error(self, ws, error):
        LOGGER.error('Websocket error: %r', error)
        os._exit(1)

    def on_close(self, ws):
        LOGGER.error('Websocket closed')
        os._exit(1)

    def run_forever(self):
        rtm = self.slack_api.get('https://slack.com/api/rtm.start').json()
        if not rtm['ok']:
            raise Exception('Failed to get RTM data: %r' % rtm)
        self.translator.slack_init(rtm['users'], rtm['channels'], rtm['team'])
        ws = websocket.WebSocketApp(
            rtm['url'],
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        ws.run_forever(ping_interval=5, ping_timeout=5)
        LOGGER.error('slack run_forever stopped')
        os._exit(1)

    def join_channel(self, name):
        LOGGER.debug('Joining slack channel %r', name)
        ret = self.slack_api.post(
            'https://slack.com/api/channels.join',
            data={'name': name}
        ).json()
        if not ret['ok']:
            raise Exception('Failed to join channe: %r' % ret)

    def leave_channel(self, channel_id):
        LOGGER.debug('Leaving slack channel %r', channel_id)
        ret = self.slack_api.post(
            'https://slack.com/api/channels.leave',
            data={'channel': channel_id}
        ).json()
        if not ret['ok']:
            raise Exception('Failed to join channe: %r' % ret)

    def send_message(self, msg):
        LOGGER.debug('Sending slack message %r', msg)
        ret = self.slack_api.post(
            'https://slack.com/api/chat.postMessage',
            data=msg
        ).json()
        if not ret['ok']:
            raise Exception('Failed to send message: %r', ret)


def main(config_uri, email, public):
    pyramid.paster.setup_logging(config_uri)
    settings = pyramid.paster.get_appsettings(config_uri)
    sessionmaker = slack_mirror.get_sessionmaker(settings)
    db = sessionmaker()

    with transaction.manager:
        user = db.query(User).filter(User.email == email).one()
        if public:
            translator = PublicTranslator()
        else:
            translator = PrivateTranslator(user.email)

        slack_api = user.slack_api
        zulip_api = zulip_client.Client(
            email=user.email,
            api_key=user.zulip_key,
            client='JabberMirror/slack',
        )

    slack = translator.slack = Slack(translator, slack_api)
    zulip = translator.zulip = Zulip(translator, zulip_api)

    threads = []

    slack_thread = threading.Thread(name='slack_thread', target=slack.run_forever)
    slack_thread.daemon = True
    slack_thread.start()
    threads.append(slack_thread)

    zulip_thread = threading.Thread(name='zulip_thread', target=zulip.run_forever)
    zulip_thread.daemon = True
    zulip_thread.start()
    threads.append(zulip_thread)

    LOGGER.info('Started mirror for %r', email)

    while threads:
        thread = threads[-1]
        if thread.is_alive():
            thread.join(1)
        else:
            threads.pop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('config_uri')
    parser.add_argument('email')
    parser.add_argument('--public', default=False, action='store_true')
    args = parser.parse_args()
    main(args.config_uri, args.email, args.public)

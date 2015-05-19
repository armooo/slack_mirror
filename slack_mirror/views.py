import logging
from datetime import datetime

from pyramid.i18n import TranslationStringFactory
from pyramid.security import remember, forget, authenticated_userid
from pyramid.view import view_config, forbidden_view_config
from pyramid.httpexceptions import HTTPFound
from sqlalchemy.orm.exc import NoResultFound

from slack_mirror.models import User, get_service, add_bot_config
from slack_mirror import api


LOGGER = logging.getLogger(__name__)


_ = TranslationStringFactory('slack_mirror')


@view_config(route_name='login')
@forbidden_view_config()
def oauth2_login(request, force=False, goto=None):
    param_goto = request.params.get('goto')
    goto = goto or param_goto or request.referer or '/'

    kwargs = {}
    if force:
        kwargs['approval_prompt'] = 'force'

    service = get_service(request.registry.settings)
    url = service.get_authorize_url(
        redirect_uri=request.route_url('oauth2_callback'),
        access_type='offline',
        response_type='code',
        scope='identify,client',
        state=goto,
        team=request.registry.settings['slack.team_id'],
        **kwargs)
    LOGGER.debug('Redirecting to google oauth2')
    return HTTPFound(location=url)


@view_config(route_name='logout')
def logout(request):
    headers = forget(request)
    LOGGER.debug('Logout now')
    return HTTPFound(location=request.route_url('home'), headers=headers)


@view_config(route_name='oauth2_callback',
             renderer='templates/login_result.jinja2')
def oauth2_callback(request):
    data = {}
    if 'error' in request.params:
        data['error'] = 'NO_AUTH'
        return data

    service = get_service(request.registry.settings)
    access = service.get_raw_access_token(
        data=dict(
            code=request.params['code'],
            redirect_uri=request.route_url('oauth2_callback'),
        )
    ).json()
    if 'error' in access:
        LOGGER.info('Failed to get the oauth token: %s', access['error'])
        return oauth2_login(request, request.params['state'])

    access_token = access['access_token']

    session = service.get_session(access_token)

    auth_info = session.get(
        'https://slack.com/api/auth.test',
        params={'token': access_token},
    ).json()
    if not auth_info['ok']:
        data['error'] = 'NO_AUTH'
        return data
    user_id = auth_info['user_id']

    user_info = session.get(
        'https://slack.com/api/users.info',
        params={
            'token': access_token,
            'user': user_id,
        },
    ).json()
    if not user_info['ok']:
        data['error'] = 'NO_AUTH'
        return data

    email = user_info['user']['profile']['email']

    try:
        user = request.db.query(User).filter(User.id == user_id).one()
    except NoResultFound:
        user = User(user_id, email, access_token)
        request.db.add(user)
        add_bot_config(request.registry.settings, user)
        api.reload_config(request.registry.settings)

    user.last_log = datetime.utcnow()
    user.email = email
    user.access_token = access_token

    headers = remember(request, user_id)
    return HTTPFound(location=request.params['state'], headers=headers)


@view_config(
    route_name='home',
    request_method='GET',
    permission='loggedin',
    renderer='templates/index.jinja2',
)
def index(request):
    user_id = authenticated_userid(request)
    user = request.db.query(User).filter(User.id == user_id).one()

    if user.zulip_key is None:
        return HTTPFound(location=request.route_url('zulip_key'))

    return {
        'state': api.get_bot_state(request.registry.settings, user)
    }


@view_config(
    route_name='zulip_key',
    request_method='GET',
    permission='loggedin',
    renderer='templates/zulip_key.jinja2',
)
def zulip_key(request):
    user_id = authenticated_userid(request)
    user = request.db.query(User).filter(User.id == user_id).one()

    return {'zulip_key': user.zulip_key or ''}


@view_config(
    route_name='zulip_key',
    request_method='POST',
    permission='loggedin',
    renderer='templates/zulip_key.jinja2',
)
def set_zulip_key(request):
    user_id = authenticated_userid(request)
    user = request.db.query(User).filter(User.id == user_id).one()
    user.zulip_key = request.POST['zulip_key']

    return HTTPFound(location=request.route_url('home'))


@view_config(
    route_name='start_mirror',
    request_method='POST',
    permission='loggedin',
)
def start_mirror(request):
    user_id = authenticated_userid(request)
    user = request.db.query(User).filter(User.id == user_id).one()

    api.start_bot(request.registry.settings, user)

    return HTTPFound(location=request.route_url('home'))


@view_config(
    route_name='stop_mirror',
    request_method='POST',
    permission='loggedin',
)
def stop_mirror(request):
    user_id = authenticated_userid(request)
    user = request.db.query(User).filter(User.id == user_id).one()

    api.stop_bot(request.registry.settings, user)

    return HTTPFound(location=request.route_url('home'))

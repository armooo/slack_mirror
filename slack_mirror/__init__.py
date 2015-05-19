from pyramid.config import Configurator
from pyramid.authentication import AuthTktAuthenticationPolicy
from pyramid.authorization import ACLAuthorizationPolicy
from sqlalchemy import engine_from_config
from sqlalchemy.orm import sessionmaker
from zope.sqlalchemy import ZopeTransactionExtension
from pyramid.security import Allow, Authenticated


class RootFactory(object):
    __acl__ = [(Allow, Authenticated, 'loggedin')]

    def __init__(self, request):
        pass


def get_sessionmaker(settings):
    engine = engine_from_config(settings, 'sqlalchemy.')
    return sessionmaker(bind=engine, extension=ZopeTransactionExtension())


def db(request):
    maker = request.registry.settings['db.sessionmaker']
    return maker()


def main(global_config, **settings):
    """ This function returns a WSGI application.

    It is usually called by the PasteDeploy framework during
    ``paster serve``.
    """
    settings = dict(settings)
    settings.setdefault('jinja2.i18n.domain', 'slack_mirror')

    config = Configurator(root_factory=RootFactory, settings=settings)
    maker = get_sessionmaker(settings)
    config.add_settings({'db.sessionmaker': maker})
    config.add_request_method(db, reify=True)

    config.add_translation_dirs('locale/')
    config.include('pyramid_jinja2')

    authn_policy = AuthTktAuthenticationPolicy(settings['tkt_secret'])
    config.set_authentication_policy(authn_policy)

    authz_policy = ACLAuthorizationPolicy()
    config.set_authorization_policy(authz_policy)

    config.add_static_view('static', 'static', cache_max_age=3600)

    config.add_route('home', '/')
    config.add_route('zulip_key', '/zulip_key')
    config.add_route('login', '/_login')
    config.add_route('logout', '/_logout')
    config.add_route('oauth2_callback', '/oauth2callback')
    config.add_route('start_mirror', '/start_mirror')
    config.add_route('stop_mirror', '/stop_mirror')
    config.scan()
    return config.make_wsgi_app()

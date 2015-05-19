import supervisor.xmlrpc
import xmlrpclib


def get_proxy(settings):
    return xmlrpclib.ServerProxy(
        'http://127.0.0.1',
        transport=supervisor.xmlrpc.SupervisorTransport(
            None,
            None,
            serverurl=settings['supervisor.sock_path'],
        )
    )


def reload_config(settings):
    proxy = get_proxy(settings)
    changes = proxy.supervisor.reloadConfig()
    for process_name in changes[0][0]:
        proxy.supervisor.addProcessGroup(process_name)


def get_bot_state(settings, user):
    proxy = get_proxy(settings)
    try:
        state = proxy.supervisor.getProcessInfo(user.email)
        stdout = proxy.supervisor.tailProcessStdoutLog(user.email, 0, 5000)
        stderr = proxy.supervisor.tailProcessStderrLog(user.email, 0, 5000)
    except:
        raise
        return {'state': 'Unknown', 'uptime': 'Unknown'}
    return {
        'state': state['statename'],
        'uptime': state['description'],
        'stdout': stdout[0],
        'stderr': stderr[0],
    }


def start_bot(settings, user):
    proxy = get_proxy(settings)
    proxy.supervisor.startProcess(user.email)


def stop_bot(settings, user):
    proxy = get_proxy(settings)
    proxy.supervisor.stopProcess(user.email)

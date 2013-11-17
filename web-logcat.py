import json
from wsgiref.simple_server import make_server
from gevent import monkey, sleep
from gevent.queue import Queue

monkey.patch_all()

import gevent
from gevent import subprocess, pywsgi
from gevent.pywsgi import WSGIServer
from geventwebsocket import WebSocketHandler
from pyramid.config import Configurator
from pyramid.view import view_config
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.pool import StaticPool
from athanor import Session, Base

import re
import sys
import time
import logging


def get_engine():
    return create_engine('sqlite:///:memory:',
                         echo=False,
                         connect_args={'check_same_thread': False},
                         poolclass=StaticPool)

class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    date = Column(String)
    time = Column(String)
    pid = Column(String)
    tid = Column(String)
    level = Column(String)
    tag = Column(String)
    message = Column(String)

clean_lines = lambda lines: (line.strip() for line in lines)

header_pattern = re.compile(r'\[ (?P<date>\d\d-\d\d) (?P<time>\d\d:\d\d:\d\d\.\d\d\d)\s+(?P<pid>\d+):\s*(?P<tid>\d+) (?P<level>\w)\/(?P<tag>\w+) \]')

Listeners = set()

def start_logcat():
    p = subprocess.Popen(['env', 'adb', 'logcat', '-v', 'long'], stdout=subprocess.PIPE, bufsize=1)
    return clean_lines(p.stdout)

def try_next(i):
    try:
        return i.next()
    except StopIteration, e:
        return None

def rate(log, iterable, period=10):
    every = 10
    last = 0

    t1 = time.time()
    for i, obj in enumerate(iterable):

        t2 = time.time()
        delta = t2 - t1

        if delta > period:
            log.info('%d items (%.2f items/sec)', i, (i-last)/delta)
            t1 = t2
            last = i

        yield obj

def process_logcat(logcat):

    while True:

        line = try_next(logcat)

        if line is None:
            break

        m = header_pattern.match(line)
        if m:
            data = m.groupdict()
            data['message'] = try_next(logcat)
            yield data

def logcat_main():

    log = logging.getLogger('logcat')
    log.info('starting adb...')

    logcat = start_logcat()
    messages = rate(log, process_logcat(logcat))

    session = Session()

    for message in messages:
        m = Message(**message)
        session.add(m)
        session.commit()

        for listener in Listeners:
            listener.put(message)


@view_config(route_name='root', renderer='base.mako')
def root(request):
    return {}

@view_config(route_name='logcat', renderer='string')
def logcat(request):

    queue = Queue()
    Listeners.add(queue)

    if request.environ.get('wsgi.websocket'):
        ws = request.environ['wsgi.websocket']
        while True:
            message = queue.get()
            ws.send(json.dumps(message))

    Listeners.remove(queue)

    return {}

def web_main():

    log = logging.getLogger('web')

    def server_factory(global_conf, host, port):
        port = int(port)

        def serve(app):
            server = WSGIServer(('', port), app, )
            log.info('serving on port %s...', port)
            server.serve_forever()

        return serve

    port = 8000
    log.info('starting web server on port %s', port)

    config = Configurator(
        settings={
        'mako.directories': 'templates',
        'reload_templates': True
    })
    config.add_static_view('static', path='static')

    config.add_route('root', '/')
    config.add_route('logcat', '/logcat')

    config.scan()

    app = config.make_wsgi_app()

    http_server = WSGIServer(('', 8000), app, handler_class=WebSocketHandler)
    http_server.serve_forever()

def main(options, args):

    engine = get_engine()
    Session.configure(bind=engine)
    Base.metadata.create_all(engine)

    logcat_thread = gevent.spawn(logcat_main)
    web_thread = gevent.spawn(web_main())
    web_thread.join()

    gevent.joinall([logcat_thread, web_thread])

if __name__ == '__main__':

    logging.basicConfig(level=logging.DEBUG)

    try:
        rv = main(None, sys.argv[1:])
    except KeyboardInterrupt, e:
        rv = 0

    sys.exit(rv)
from datetime import date, datetime
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

build_message = lambda type, **kwargs: dict(type=type, payload=kwargs)
log_message = lambda data: build_message('log', **data)
status_message = lambda status: build_message('status', status=status)

def get_engine():
    return create_engine('sqlite:///:memory:',
                         echo=False,
                         connect_args={'check_same_thread': False},
                         poolclass=StaticPool)

class Message(Base):
    __tablename__ = 'messages'

    timestamp = Column(Integer, primary_key=True)
    seq = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String)
    time = Column(String)
    pid = Column(String)
    tid = Column(String)
    level = Column(String)
    tag = Column(String)
    message = Column(String)

clean_lines = lambda lines: (line.strip() for line in lines)

header_pattern = re.compile(r'\[ (?P<date>\d\d-\d\d) (?P<time>\d\d:\d\d:\d\d\.\d\d\d)\s+(?P<pid>\d+):\s*(?P<tid>\d+) (?P<level>\w)\/(?P<tag>\w+) \]')
waiting_pattern = re.compile(r'-\swaiting\sfor\sdevice\s-')

Listeners = set()

def start_logcat():
    return subprocess.Popen(['env', 'adb', 'logcat', '-v', 'long'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1)

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

def process_logcat_stdout(logcat):
    lines = clean_lines(logcat.stdout)

    seq = 0
    while True:

        line = try_next(lines)

        if line is None:
            break

        m = header_pattern.match(line)
        if m:
            data = m.groupdict()
            t = '%s-%s %s' % (date.today().year, data['date'], data['time'])
            d = datetime.strptime(t, '%Y-%m-%d %H:%M:%S.%f')
            t = (time.mktime(d.timetuple()) * 1000) + (d.microsecond / 1000)
            data['timestamp'] = t
            data['message'] = try_next(lines)
            seq += 1
            data['seq'] = seq
            yield data

def fanout(message):
    for listener in Listeners:
        listener.put(message)

def store_message(session, message):
    m = Message(**message)
    session.add(m)
    session.commit()

def process_logout_stderr(logcat):
    lines = clean_lines(logcat.stderr)

    log = logging.getLogger('logcat-stderr-reader')

    log.info('started')
    while True:

        line = try_next(lines)
        if line is None:
            break

        m = waiting_pattern.match(line)
        if m:
            log.info('waiting for adb')
            fanout(status_message('waiting for device...'))

    log.info('finished')

def logcat_main():

    log = logging.getLogger('logcat-main')

    last = 0
    while True:
        log.info('starting adb...')
        logcat = start_logcat()

        fanout(status_message('ADB started...'))

        gevent.spawn(process_logout_stderr, logcat)

        messages = rate(log, process_logcat_stdout(logcat))

        session = Session()

        for message in messages:

            if message['timestamp'] < last:
                continue

            last = max(last, message['timestamp'])

            store_message(session, message)
            fanout(log_message(message))

        logcat.wait()

        log.info('logcat finished with retcode = %s', logcat.returncode)
        fanout(status_message('ADB terminated, restarting'))


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
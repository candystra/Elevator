# -*- coding: utf-8 -*-

# Copyright (c) 2012 theo crevon
#
# See the file LICENSE for copying permission.

import zmq
import logging
import threading

from .constants import FAILURE_STATUS, REQUEST_ERROR, WORKER_HALT
from .env import Environment
from .api import Handler
from .message import Request, MessageFormatError, ResponseContent, ResponseHeader
from .db import DatabasesHandler
from .utils.patterns import enum

activity_logger = logging.getLogger("activity_logger")
errors_logger = logging.getLogger("errors_logger")


class HaltException(Exception):
    pass


class Worker(threading.Thread):
    def __init__(self, zmq_context, databases, *args, **kwargs):
        threading.Thread.__init__(self)
        self.STATES = enum('RUNNING', 'IDLE', 'STOPPED')
        self.zmq_context = zmq_context
        self.state = self.STATES.RUNNING
        self.databases = databases
        self.env = Environment()
        self.socket = self.zmq_context.socket(zmq.XREQ)
        self.handler = Handler(databases)
        self.processing = False

    def run(self):
        self.socket.connect('inproc://elevator')
        msg = None

        while (self.state == self.STATES.RUNNING):
            try:
                sender_id, msg = self.socket.recv_multipart(copy=False)
                # If worker pool sends a WORKER_HALT, then close
                # and return to stop execution
                if sender_id.bytes == WORKER_HALT:  # copy=False -> zmq.Frame
                    raise HaltException("Gracefully stopping worker %r" % self.ident)
            except zmq.ZMQError as e:
                self.state = self.STATES.STOPPED
                errors_logger.warning('Worker %r encountered and error,'
                                      ' and was forced to stop' % self.ident)
                return
            except HaltException as e:
                activity_logger.info(e)
                return self.close()

            self.processing = True

            try:
                message = Request(msg)
            except MessageFormatError as e:
                errors_logger.exception(e.value)
                header = ResponseHeader(status=FAILURE_STATUS,
                                        err_code=REQUEST_ERROR,
                                        err_msg=e.value)
                content = ResponseContent(datas={})
                self.socket.send_multipart([sender_id, header, content], copy=False)
                continue

            # Handle message, and execute the requested
            # command in leveldb
            header, response = self.handler.command(message)

            self.socket.send_multipart([sender_id, header, response], flags=zmq.NOBLOCK, copy=False)
            self.processing = False

    def close(self):
        self.state = self.STATES.STOPPED

        if not self.socket.closed:
            self.socket.close()


class WorkersPool():
    def __init__(self, workers_count=4, **kwargs):
        env = Environment()
        database_store = env['global']['database_store']
        databases_storage = env['global']['databases_storage_path']
        self.databases = DatabasesHandler(database_store, databases_storage)
        self.pool = []

        self.zmq_context = zmq.Context()
        self.socket = self.zmq_context.socket(zmq.XREQ)
        self.socket.bind('inproc://elevator')
        self.init_workers(workers_count)

    def __del__(self):
        while any(worker.isAlive() for worker in self.pool):
            self.socket.send_multipart([WORKER_HALT, ""])

        for worker in self.pool:
            worker.join()

        self.socket.close()

    def init_workers(self, count):
        pos = 0

        while pos < count:
            worker = Worker(self.zmq_context, self.databases)
            worker.start()
            self.pool.append(worker)
            pos += 1

#!/usr/bin/env python
# -*- coding: utf-8 -*-

import zmq
import leveldb
import threading
import ujson as json

from api import Handler
from utils.patterns import enum
from utils.decorators import cached_property


class MessageFormatError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


class Message(object):
    """Handler objects for frontend->backend objects messages"""
    def __init__(self, message):
        if not self.is_valid(message):
            raise MessageFormatError("Bad Message format")
        self.id = message[0]
        self.op_code = message[1]
        self.data = json.loads(message[2])
        self.reply = [self.id]


    def is_valid(self, message):
        if len(message) != 3:
            return False
        return True


class Worker(threading.Thread):
    def __init__(self, context, db):
        threading.Thread.__init__(self)
        self.STATES = enum('RUNNING', 'IDLE', 'STOPPED')
        self.context = context
        self.state = self.STATES.RUNNING
        self.db = db
        self.socket = context.socket(zmq.XREQ)
        self.handler = Handler(db)


    def run(self):
        self.socket.connect('inproc://leveldb')
        msg = None

        while (self.state == self.STATES.RUNNING):
            try:
                msg = self.socket.recv_multipart()
            except zmq.ZMQError:
                self.state = STATES.STOPPED
                continue

            processing = True
            try:
                message = Message(msg)
            except MessageFormatError:
                value = 'None'
                reply = [msg[0], value]
                self.socket.send_multipart(reply)
                continue
            # Handle message, and execute the requested
            # command in leveldb
            reply = [message.id]
            value = self.handler.command(message)
            reply.append(value)
            self.socket.send_multipart(reply)
            processing = False


    def close(self):
        self.running = False
        while self.processing:
            sleep(1)
        self.socket.close()


class Backend():
    def __init__(self, db):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.XREQ)
        self.socket.bind('inproc://leveldb')
        self.db = leveldb.LevelDB(db)
        self.workers_pool = []
        self.init_workers()


    def __del__(self):
        [worker.close() for worker in self.workers_pool]
        self.socket.close()
        self.context.term()


    def init_workers(self, count=4):
        pos = 0

        while pos < count:
            worker = Worker(self.context, self.db)
            worker.start()
            self.workers_pool.append(worker)
            pos += 1


class Frontend():
    def __init__(self, host):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.XREP)
        self.socket.bind(host)

    def __del__(self):
        self.socket.close()
        self.context.term()

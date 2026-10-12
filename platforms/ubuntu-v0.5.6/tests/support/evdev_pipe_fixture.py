"""Test-only evdev boundary: real fd/select/read, mocked hardware/ioctls/uinput."""
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace as NS

EV_SYN,EV_KEY,EV_REL=0,1,2
ecodes=NS(EV_SYN=0,EV_KEY=1,EV_LED=17,SYN_REPORT=0,SYN_DROPPED=3,BUS_USB=3,ecodes={'BTN_SIDE':275,'BTN_EXTRA':276})

def record(value):
    with open(os.environ['VB_HW_LOG'],'a') as f: f.write(json.dumps(value)+'\n')

def list_devices(**kwargs): return ['/dev/input/test-mouse']

class InputDevice:
    name='ROG simulated evdev mouse';path='/dev/input/test-mouse';phys='usb/test'
    def __init__(self,path):
        self.fd,self.writer=os.pipe();os.set_blocking(self.fd,False)
        self.done=threading.Event();self.keys=set()
        # Regression: a queue overrun before proxy discovery must not kill startup.
        self._send([[0,3,0],[0,0,0]])
        self.thread=threading.Thread(target=self._commands,daemon=True);self.thread.start()
    def _send(self,events):
        for t,c,v in events:
            if t==1:
                if v==0:self.keys.discard(c)
                elif v==1:self.keys.add(c)
        os.write(self.writer,(json.dumps(events)+'\n').encode())
    def _commands(self):
        mailbox=Path(os.environ['VB_TRIGGER_COMMAND'])
        while not self.done.wait(.005):
            try:
                text=mailbox.read_text();mailbox.unlink()
            except FileNotFoundError:continue
            self._send(json.loads(text))
    def fileno(self):return self.fd
    def read(self):
        data=os.read(self.fd,65536)
        for line in data.splitlines():
            for t,c,v in json.loads(line):yield NS(type=t,code=c,value=v)
    def capabilities(self,**kwargs):return {1:[272,273,274,275,276],2:[0,1,8,11]}
    def active_keys(self):return list(self.keys)
    def grab(self):record(['grabbed'])
    def ungrab(self):record(['ungrabbed'])
    def close(self):
        self.done.set();self.thread.join(1)
        os.close(self.fd);os.close(self.writer);record(['device-closed'])

class UInput:
    def __init__(self,*args,**kwargs):
        self.proxy='proxy' in kwargs.get('name','')
        record(['proxy-created' if self.proxy else 'created',kwargs.get('name','')])
    @classmethod
    def from_device(cls,dev,**kwargs):return cls(**kwargs)
    def write(self,*args):record(['proxy',*args] if self.proxy else list(args))
    def syn(self):record(['proxy-syn' if self.proxy else 'syn'])
    def close(self):record(['proxy-closed' if self.proxy else 'closed'])

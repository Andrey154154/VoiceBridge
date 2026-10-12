"""Real trigger algorithms, explicit simulated kernel queues/ioctls (not hardware)."""
from collections import deque
import errno
import socket
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from voice_bridge.triggers.evdev_trigger import EvdevHoldTrigger

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REPORT, DROPPED, SIDE, LEFT = 0, 3, 275, 272

def ev(t,c,v=0): return NS(type=t,code=c,value=v)
def frame(*events): return list(events)+[ev(EV_SYN,REPORT)]

class Device:
    name='ROG simulated mouse'; path='/dev/input/event-test'; phys='usb/test'
    def __init__(self):
        self.keys=set(); self.backlog=deque(); self.reads=0
        self.grabbed=False; self.ungrabs=0; self.closed=False
        self.caps={1:[272,273,274,275,276],2:[0,1,8,11]}
    def capabilities(self, **kw): return self.caps
    def active_keys(self): return list(self.keys)
    def read(self):
        self.reads+=1
        if not self.backlog: raise BlockingIOError(errno.EAGAIN,'empty')
        return iter(self.backlog.popleft())
    def grab(self): self.grabbed=True
    def ungrab(self): self.grabbed=False;self.ungrabs+=1
    def close(self): self.closed=True

class Proxy:
    def __init__(self): self.writes=[];self.closed=False
    def write(self,*args): self.writes.append(args)
    def syn(self): self.writes.append((0,0,0))
    def close(self): self.closed=True

@pytest.fixture
def rig(monkeypatch):
    d=Device();p=Proxy()
    m=NS(ecodes=NS(EV_KEY=1,EV_SYN=0,SYN_REPORT=0,SYN_DROPPED=3,ecodes={'BTN_SIDE':SIDE}),
         UInput=NS(from_device=Mock(return_value=p)),list_devices=lambda **kw:[d.path],InputDevice=lambda path:d)
    monkeypatch.setitem(sys.modules,'evdev',m)
    t=EvdevHoldTrigger(consume=True);t._settle_seconds=0
    t.on_ready=Mock();t.on_cancel=Mock();press=Mock();release=Mock()
    return NS(d=d,p=p,m=m,t=t,press=press,release=release)

def run(r, events):
    r.t._events=lambda _:iter(events)
    r.t.run(r.press,r.release)


def test_startup_discards_backlog_including_drop_and_old_click(rig):
    r=rig
    r.d.backlog.append([ev(0,3)]+frame(ev(1,SIDE,1),ev(1,SIDE,0),ev(2,0,900)))
    run(r,frame(ev(2,0,7)))
    r.t.on_cancel.assert_not_called();r.press.assert_not_called();r.release.assert_not_called()
    r.t.on_ready.assert_called_once()
    assert r.p.writes==[(2,0,7),(0,0,0)] and r.d.reads>=3


def test_settle_continuously_drains_simulated_8000hz_mouse(rig,monkeypatch):
    """Finite queue model: old 500ms sleep fills 4000 packets into a 64-packet queue."""
    r=rig;now=[0.0];overruns=[];waits=[]
    import voice_bridge.triggers.evdev_trigger as mod
    monkeypatch.setattr(mod.time,'monotonic',lambda:now[0])
    class ClockStop:
        def is_set(self): return False
        def wait(self,seconds):
            waits.append(seconds); now[0]+=seconds
            packets=max(1,round(8000*seconds))
            if len(r.d.backlog)+packets>64:
                overruns.append(1);r.d.backlog.clear();r.d.backlog.append([ev(0,3),ev(0,0)])
            r.d.backlog.extend(frame(ev(2,0,1)) for _ in range(min(packets,64)))
            return False
    r.t._stop_event=ClockStop()
    assert r.t._prepare_input(r.d,settle=0.5)
    assert not overruns and max(waits)<=0.002 and r.d.reads>500
    # Same model and input rate with 0.5.5's exact blind wait demonstrates overrun.
    r.t._stop_event.wait(0.5)
    assert overruns and len(r.d.backlog)>0


def test_startup_cancel_never_grabs_or_signals_ready(rig):
    r=rig;r.t.stop();run(r,[])
    assert not r.d.grabbed and r.d.ungrabs==0 and r.p.closed and r.d.closed
    r.t.on_ready.assert_not_called()


def test_baseline_hold_cannot_start_dictation(rig):
    r=rig
    real_grab=r.d.grab
    def grab(): real_grab();r.d.keys={SIDE}
    r.d.grab=grab
    run(r,frame(ev(1,SIDE,0))+frame(ev(1,SIDE,1))+frame(ev(1,SIDE,0)))
    r.press.assert_called_once();r.release.assert_called_once()
    assert [x for x in r.p.writes if x[0]==1]==[(1,SIDE,1),(1,SIDE,0)]


def test_drop_recovers_and_does_not_reload_or_recreate_proxy(rig):
    r=rig
    run(r, [ev(0,3),ev(2,0,999),ev(1,SIDE,1),ev(0,0)]+frame(ev(2,0,2))+frame(ev(1,SIDE,1),ev(1,SIDE,0)))
    r.t.on_cancel.assert_called_once();r.t.on_ready.assert_called_once()
    r.press.assert_called_once();r.release.assert_called_once();r.m.UInput.from_device.assert_called_once()
    assert (2,0,999) not in r.p.writes and (2,0,2) in r.p.writes
    assert r.d.ungrabs==1 and r.p.closed


def test_partial_frame_is_discarded_without_a_press_or_movement(rig):
    r=rig
    run(r,[ev(1,SIDE,1),ev(2,0,999),ev(0,3),ev(1,SIDE,0),ev(0,0)]+frame(ev(2,8,1)))
    r.press.assert_not_called();r.release.assert_not_called()
    assert (2,0,999) not in r.p.writes and (2,8,1) in r.p.writes


def test_loss_cancels_hold_without_release_and_next_pair_works(rig):
    r=rig
    def stream():
        yield from frame(ev(1,SIDE,1))
        yield ev(0,3)
        r.d.keys={SIDE}
        yield ev(0,0)
        yield from frame(ev(1,SIDE,2))
        r.d.keys.clear()
        yield from frame(ev(1,SIDE,0))  # not an ASR submit
        yield from frame(ev(1,SIDE,1),ev(1,SIDE,0))
    run(r,stream())
    assert r.press.call_count==2 and r.release.call_count==1
    r.t.on_cancel.assert_called_once()
    assert not any(x[0]==1 for x in r.p.writes)


@pytest.mark.parametrize('held_after',[False,True])
def test_other_button_state_is_reconciled_without_stuck_or_duplicate_down(rig,held_after):
    r=rig
    def stream():
        yield from frame(ev(1,LEFT,1))
        yield ev(0,3);r.d.keys={LEFT} if held_after else set();yield ev(0,0)
        if held_after: yield from frame(ev(1,LEFT,0))
    run(r,stream())
    assert [x for x in r.p.writes if x[0]==1]==[(1,LEFT,1),(1,LEFT,0)]


def test_unknown_held_target_is_not_fabricated_as_dictation(rig):
    r=rig
    def stream():
        yield ev(0,3);r.d.keys={SIDE,LEFT};yield ev(0,0)
        yield from frame(ev(1,SIDE,1),ev(1,SIDE,2),ev(1,SIDE,0),ev(1,LEFT,0))
    run(r,stream())
    r.press.assert_not_called();r.release.assert_not_called()
    assert [x for x in r.p.writes if x[0]==1]==[(1,LEFT,1),(1,LEFT,0)]


def test_paused_target_hold_remains_desktop_owned_across_resync_and_enable(rig):
    r=rig;r.t.set_enabled(False)
    def stream():
        yield from frame(ev(1,SIDE,1));yield ev(0,3)
        r.d.keys={SIDE};r.t.set_enabled(True);yield ev(0,0)
        yield from frame(ev(1,SIDE,0))
        yield from frame(ev(1,SIDE,1),ev(1,SIDE,0))
    run(r,stream())
    assert [x for x in r.p.writes if x[0]==1]==[(1,SIDE,1),(1,SIDE,0)]
    r.press.assert_called_once();r.release.assert_called_once()


def test_multiple_drops_before_report_cancel_once_and_discard_corrupt_edges(rig):
    r=rig
    run(r,[ev(0,3),ev(1,SIDE,1),ev(0,3),ev(1,SIDE,0),ev(0,0)])
    r.t.on_cancel.assert_called_once();r.press.assert_not_called();r.release.assert_not_called()


def test_repeated_overload_releases_grab_instead_of_infinite_loop(rig):
    r=rig
    with pytest.raises(RuntimeError,match='повторная перегрузка'):
        run(r,[e for _ in range(9) for e in (ev(0,3),ev(0,0))])
    assert r.d.ungrabs==1 and r.p.closed and r.d.closed


def test_old_overflow_burst_does_not_count_towards_later_rate_limit(rig,monkeypatch):
    r=rig;clock=[10.0]
    import voice_bridge.triggers.evdev_trigger as mod
    monkeypatch.setattr(mod.time,'monotonic',lambda:clock[0])
    def stream():
        for _ in range(8): yield ev(0,3);yield ev(0,0)
        clock[0]+=11
        yield ev(0,3);yield ev(0,0)
    run(r,stream())
    assert len(r.t._sync_times)==1


def test_disappeared_device_during_snapshot_releases_proxy(rig):
    r=rig
    def stream():
        yield from frame(ev(1,LEFT,1));yield ev(0,3)
        r.d.active_keys=Mock(side_effect=OSError(errno.ENODEV,'unplugged'))
        yield ev(0,0)
    with pytest.raises(OSError): run(r,stream())
    assert (1,LEFT,0) in r.p.writes and r.d.ungrabs==1 and r.p.closed and r.d.closed


def test_missing_report_times_out_even_without_further_input(rig,monkeypatch):
    r=rig;r.t._sync_timeout=.01
    import voice_bridge.triggers.evdev_trigger as mod
    real_time=time.monotonic
    def select(_r,_w,_e,timeout):
        time.sleep(.02)
        return [],[],[]
    monkeypatch.setattr(mod.select,'select',select)
    original=r.t._events
    def stream(d):
        yield ev(0,3)
        yield from original(d)
    r.t._events=stream
    start=real_time()
    with pytest.raises(RuntimeError,match='не восстановился'): r.t.run(r.press,r.release)
    assert real_time()-start<1 and r.d.ungrabs==1 and r.p.closed


def test_stop_mid_recovery_is_clean(rig):
    r=rig
    def stream():
        yield ev(0,3);r.t.stop()
    run(r,stream())
    assert r.d.ungrabs==1 and r.p.closed


def test_read_startup_error_does_not_grab(rig):
    r=rig;r.d.read=Mock(side_effect=PermissionError('read denied'))
    with pytest.raises(PermissionError): run(r,[])
    assert r.d.ungrabs==0 and r.p.closed and r.d.closed


def test_multitouch_device_not_grabbed_with_mouse_only_recovery(rig):
    r=rig;r.d.caps[3]=[0x2f,0x35,0x36]
    with pytest.raises(RuntimeError,match='multitouch'): run(r,[])
    assert r.d.ungrabs==0 and r.p.closed


def test_non_multitouch_absolute_axes_and_switches_resynced(rig):
    r=rig;r.d.caps.update({3:[0],5:[0,1]})
    r.d.absinfo=lambda code:NS(value=42)
    r.d.switches=lambda:[1]
    run(r,[ev(0,3),ev(0,0)])
    assert (3,0,42) in r.p.writes and (5,0,0) in r.p.writes and (5,1,1) in r.p.writes


def test_packet_bound_without_syn_report_is_rejected(rig):
    r=rig
    with pytest.raises(RuntimeError,match='границы пакета'):
        run(r,[ev(2,0,1) for _ in range(1025)])
    assert r.d.ungrabs==1 and r.p.closed and not r.p.writes


def test_observer_also_recovers_and_never_turns_lost_release_into_asr(rig):
    r=rig;r.t.consume=False
    def stream():
        yield from frame(ev(1,SIDE,1));yield ev(0,3)
        r.d.keys={SIDE};yield ev(0,0)
        yield from frame(ev(1,SIDE,0))
        yield from frame(ev(1,SIDE,1),ev(1,SIDE,0))
    run(r,stream())
    r.t.on_cancel.assert_called_once()
    assert r.press.call_count==2 and r.release.call_count==1 and r.d.ungrabs==0
    r.m.UInput.from_device.assert_not_called()

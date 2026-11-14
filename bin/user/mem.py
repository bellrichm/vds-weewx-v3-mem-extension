
#
# mem.py - Tom's memory add-on and Matthew's v3 pmon extension
#          combined into a Franken-extension by Vince
#
#    vinceskahan@gmail.com - 2014-1128 - original
#
# Changes:
# - changed it to use Tom's /proc method including
#     using all three memory fields, not just rss+shared
# - deleted the __main__ code, which I gave up trying to
#     get working.  Go for smoke !!!!!
# - changed variable names and Monitor name to match
#     Tom's terminology a little closer
# - commented out the pruning of old data as the default
#     It would be cool to have this configurable perhaps
#
# bugs are mine (vinceskahan@gmail.com)
#
#    2014-1127 - Happy Thanksgiving and GO EAGLES !!!!
#
#



#---------------------------
#
# (copyright for the original pmon this is based on)
#
# weewx v# $Id: mem.py 2692 2014-11-25 01:07:48Z mwall $
# Copyright 2013 Matthew Wall
#
#---------------------------


"""weewx module that records memory information.

Installation
============

Put this file in the bin/user directory.

Configuration
============

Add the following to weewx.conf:

[MemoryMonitor]
    data_binding = mem_binding

[DataBindings]
    [[mem_binding]]
        database = mem_sqlite
        manager = weewx.manager.DaySummaryManager
        table_name = archive
        schema = user.mem.schema

[Databases]
    [[mem_sqlite]]
        root = %(WEEWX_ROOT)s
        database_name = archive/mem.sdb
        driver = weedb.sqlite

[Engine]
    [[Services]]
        archive_services = ..., user.mem.MemoryMonitor
"""

import ctypes
import ctypes.util
import os
import platform
import resource
import syslog
import time

import weewx
import weeutil.weeutil
from weewx.engine import StdService
from weeutil.weeutil import to_bool

VERSION = "0.1-rmb"

def logmsg(level, msg):
    syslog.syslog(level, 'mem: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

schema = [
    ('dateTime', 'INTEGER NOT NULL PRIMARY KEY'),
    ('usUnits', 'INTEGER NOT NULL'),
    ('interval', 'INTEGER NOT NULL'),
    ('mem_size','INTEGER'),
    ('mem_rss','INTEGER'),
    ('mem_share','INTEGER'),
    ]

MB = 1024.0 * 1024.0

# libproc constants from <libproc.h>
PROC_PIDTASKINFO = 4
RUSAGE_INFO_V2 = 2


class _ProcTaskInfo(ctypes.Structure):
    """struct proc_taskinfo from <libproc.h>"""
    _fields_ = [
        ('pti_virtual_size', ctypes.c_uint64),
        ('pti_resident_size', ctypes.c_uint64),
        ('pti_total_user', ctypes.c_uint64),
        ('pti_total_system', ctypes.c_uint64),
        ('pti_threads_user', ctypes.c_uint64),
        ('pti_threads_system', ctypes.c_uint64),
        ('pti_policy', ctypes.c_int32),
        ('pti_faults', ctypes.c_int32),
        ('pti_pageins', ctypes.c_int32),
        ('pti_cow_faults', ctypes.c_int32),
        ('pti_messages_sent', ctypes.c_int32),
        ('pti_messages_received', ctypes.c_int32),
        ('pti_syscalls_mach', ctypes.c_int32),
        ('pti_syscalls_unix', ctypes.c_int32),
        ('pti_csw', ctypes.c_int32),
        ('pti_threadnum', ctypes.c_int32),
        ('pti_numrunning', ctypes.c_int32),
        ('pti_priority', ctypes.c_int32),
        ]


class _RusageInfoV2(ctypes.Structure):
    """struct rusage_info_v2 from <sys/resource.h>"""
    _fields_ = [('ri_uuid', ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            'ri_user_time',
            'ri_system_time',
            'ri_pkg_idle_wkups',
            'ri_interrupt_wkups',
            'ri_pageins',
            'ri_wired_size',
            'ri_resident_size',
            'ri_phys_footprint',
            'ri_proc_start_abstime',
            'ri_proc_exit_abstime',
            'ri_child_user_time',
            'ri_child_system_time',
            'ri_child_pkg_idle_wkups',
            'ri_child_interrupt_wkups',
            'ri_child_pageins',
            'ri_child_elapsed_abstime',
            'ri_diskio_bytesread',
            'ri_diskio_byteswritten')]


_libproc = None


def _read_proc_statm(pid):
    """Return (size, rss, shared) in bytes from /proc/<pid>/statm (Linux)."""
    with open('/proc/%d/statm' % pid) as f:
        fields = f.read().split()
    page_size = resource.getpagesize()
    size, resident, shared = [int(v) * page_size for v in fields[:3]]
    return size, resident, shared


def _read_macos(pid):
    """Return (size, rss, shared) in bytes using libproc (macOS).

    macOS has no /proc.  'size' is reported as the physical footprint
    (what Activity Monitor calls 'Memory'), not the virtual size: the latter
    includes the huge dyld shared-cache reservation (hundreds of GB) and is
    not useful for tracking a process.  There is no statm 'shared' field, so
    it is approximated by the resident pages that are not part of the physical
    footprint (i.e. shared/clean pages).
    """
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL(ctypes.util.find_library('proc')
                               or '/usr/lib/libproc.dylib')
        _libproc.proc_pidinfo.restype = ctypes.c_int
        _libproc.proc_pid_rusage.restype = ctypes.c_int

    task = _ProcTaskInfo()
    n = _libproc.proc_pidinfo(pid, PROC_PIDTASKINFO, 0,
                              ctypes.byref(task), ctypes.sizeof(task))
    if n != ctypes.sizeof(task):
        raise OSError(ctypes.get_errno(),
                      'proc_pidinfo failed for pid %d' % pid)

    resident = task.pti_resident_size

    usage = _RusageInfoV2()
    n = _libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, ctypes.byref(usage))
    if n == 0 and usage.ri_phys_footprint:
        size = usage.ri_phys_footprint
    else:
        # If the footprint is unavailable, fall back to the resident size
        # rather than the (misleadingly large) virtual size.
        size = resident

    shared = max(0, resident - size)
    return size, resident, shared


def read_memory(pid=None):
    """Return (size, rss, shared) for a process, in bytes.

    Linux uses /proc/<pid>/statm; macOS uses libproc.
    """
    if pid is None:
        pid = os.getpid()
    if os.path.exists('/proc/%d/statm' % pid):
        return _read_proc_statm(pid)
    if platform.system() == 'Darwin':
        return _read_macos(pid)
    raise OSError('no memory information available for pid %d' % pid)


class MemoryMonitor(StdService):

    def __init__(self, engine, config_dict):
        super(MemoryMonitor, self).__init__(engine, config_dict)

        d = config_dict.get('MemoryMonitor', {})
        enable = to_bool(d.get('enable', True))
        if not enable:
            loginf("mem is not enabled, exiting")
            return

        self.max_age = weeutil.weeutil.to_int(d.get('max_age', 2592000))

        # get the database parameters we need to function
        binding = d.get('data_binding', 'mem_binding')
        self.dbm = self.engine.db_binder.get_manager(data_binding=binding,
                                                     initialize=True)

        # be sure database matches the schema we have
        dbcol = self.dbm.connection.columnsOf(self.dbm.table_name)
        dbm_dict = weewx.manager.get_manager_dict(
            config_dict['DataBindings'], config_dict['Databases'], binding)
        memcol = [x[0] for x in dbm_dict['schema']]
        if dbcol != memcol:
            raise Exception('mem schema mismatch: %s != %s' % (dbcol, memcol))

        self.last_ts = None
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def shutDown(self):
        try:
            self.dbm.close()
        except:
            pass

    def new_archive_record(self, event):
        """save data to database then prune old records as needed"""
        now = int(time.time() + 0.5)
        delta = now - event.record['dateTime']
        if delta > event.record['interval'] * 60:
            logdbg("Skipping record: time difference %s too big" % delta)
            return
        if self.last_ts is not None:
            record = self.get_data(now, self.last_ts)
            if record is not None:
                self.save_data(record)
        self.last_ts = now
        if self.max_age is not None:
            self.prune_data(now - self.max_age)

    def save_data(self, record):
        """save data to database"""
        self.dbm.addRecord(record)

    def prune_data(self, ts):
        """delete records with dateTime older than ts"""
        sql = "delete from %s where dateTime < %d" % (self.dbm.table_name, ts)
        self.dbm.getSql(sql)
        try:
            # sqlite databases need some help to stay small
            self.dbm.getSql('vacuum')
        except Exception as e:
            pass

    def get_data(self, now_ts, last_ts):
        record = {}
        record['dateTime'] = now_ts
        record['usUnits'] = weewx.METRIC
        record['interval'] = int((now_ts - last_ts) / 60)
        try:
            size, resident, shared = read_memory()
        except (IOError, OSError, ValueError) as e:
            logerr('memory_info failed: %s' % e)
            return None
        record['mem_size'] = size / MB
        record['mem_rss'] = resident / MB
        record['mem_share'] = shared / MB
        return record


# what follows is a basic unit test of the memory lookup.  to run the test:
#
# cd /home/weewx
# PYTHONPATH=bin python bin/user/mem.py
#
if __name__ == "__main__":
    for i in range(3):
        size, resident, shared = read_memory()
        print('mem_size=%.3f MB  mem_rss=%.3f MB  mem_share=%.3f MB'
              % (size / MB, resident / MB, shared / MB))
        time.sleep(5)

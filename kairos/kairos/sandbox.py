"""Windows Job Object 沙箱（v2.0 R6 strict 档）：
- 内存上限 512MB（Process + Job 级）
- 进程数上限 32（防 fork 炸弹）
- KILL_ON_JOB_CLOSE：Python 侧崩溃/超时后子进程树必然回收
诚实边界：不做网络隔离（需防火墙规则/管理员权限），多用户生产不可依赖。
Job Object 分配失败时由调用方降级 basic 档并留痕。"""
import ctypes
import ctypes.wintypes as wt
import subprocess

_JOB_MEMORY_LIMIT = 512 * 1024 * 1024
_JOB_ACTIVE_PROCS = 32

# JOBOBJECT_BASIC_LIMIT_INFORMATION / EXTENDED_LIMITS 字段位
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),  # ULONG_PTR
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),  # 官方结构体字段，缺了会 BAD_LENGTH
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def run_in_job(cmd: list, popen_kwargs: dict, timeout: int = 10):
    """在 Job Object 内执行 cmd，返回 (CompletedProcess, note)。失败抛异常由调用方降级。"""
    k32 = ctypes.windll.kernel32
    hjob = k32.CreateJobObjectW(None, None)
    if not hjob:
        raise OSError(f"CreateJobObjectW failed: {ctypes.GetLastError()}")
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY |
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        info.BasicLimitInformation.ActiveProcessLimit = _JOB_ACTIVE_PROCS
        info.ProcessMemoryLimit = _JOB_MEMORY_LIMIT
        info.JobMemoryLimit = _JOB_MEMORY_LIMIT
        if not k32.SetInformationJobObject(
                hjob, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation=9
            raise OSError(f"SetInformationJobObject failed: {ctypes.GetLastError()}")

        proc = subprocess.Popen(cmd, **popen_kwargs)
        if not k32.AssignProcessToJobObject(hjob, int(proc._handle)):
            proc.kill()
            raise OSError(f"AssignProcessToJobObject failed: {ctypes.GetLastError()}")
        try:
            r = proc.communicate(timeout=timeout)
            completed = subprocess.CompletedProcess(cmd, proc.returncode,
                                                    stdout=r[0], stderr=r[1])
            note = "strict(Job Object: 内存512MB/进程≤32)"
            return completed, note
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
    finally:
        # KILL_ON_JOB_CLOSE：句柄关闭即回收整棵子进程树
        k32.CloseHandle(hjob)

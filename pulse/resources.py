"""Admission control and bounded, supervised analysis processes."""
import os
import subprocess
import threading
import time
import psutil

# Shared only for admission, never for portfolio data. Two workers per server.
_slots=threading.BoundedSemaphore(2)

class WorkerLimitError(RuntimeError):
    pass

def execute_worker(command,work,env,timeout,memory_mb=1024):
    timeout=min(max(float(timeout),.01),300)
    if not _slots.acquire(blocking=False):
        raise WorkerLimitError('Analysis capacity is busy. Retry after an active analysis finishes.')
    process=None
    try:
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        process=subprocess.Popen(command,cwd=work,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
        started=time.monotonic()
        monitored=psutil.Process(process.pid)
        while process.poll() is None:
            if time.monotonic()-started>timeout:
                raise WorkerLimitError('Analysis reached its time limit. Use a smaller export or retry when providers are available.')
            try:
                memory=monitored.memory_info().rss+sum(p.memory_info().rss for p in monitored.children(recursive=True))
                if memory>memory_mb*1024*1024:
                    raise WorkerLimitError('Analysis reached its memory limit. Use a smaller export.')
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                raise WorkerLimitError('Resource monitoring is unavailable. Analysis stopped safely.') from None
            time.sleep(.05)
        return process.returncode
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):child.kill()
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
                process.kill()
            process.wait()
        _slots.release()

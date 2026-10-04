import os

from prometheus_client import multiprocess


def child_exit(server, worker):
    multiprocess.mark_process_dead(
        worker.pid,
        path=os.environ["PROMETHEUS_MULTIPROC_DIR"],
    )

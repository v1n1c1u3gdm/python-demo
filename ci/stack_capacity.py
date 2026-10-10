"""Measure the isolated Compose fixture while one local quality pipeline runs."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

_DOCKER_TIMEOUT_SECONDS = 10
_PIPELINE_TIMEOUT_SECONDS = 3600


def _docker(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=_DOCKER_TIMEOUT_SECONDS,
    )


def _container_ids(
    project_names: tuple[str, ...], baseline_ids: set[str], run_id: str, include_stopped: bool = False
) -> tuple[set[str], set[str]]:
    stack_ids: set[str] = set()
    for project in project_names:
        result = _docker(["ps", "-q", "--filter", f"label=com.docker.compose.project={project}"])
        if result.returncode:
            raise RuntimeError("Docker could not list the isolated fixture project containers.")
        stack_ids.update(result.stdout.split())
    listing = ["ps", "--format", "{{json .}}"]
    if include_stopped:
        listing.insert(1, "--all")
    containers = _docker(listing)
    if containers.returncode:
        raise RuntimeError("Docker could not inspect running workflow container identities.")
    jobs: set[str] = set()
    for line in containers.stdout.splitlines():
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(container, dict):
            continue
        container_id = container.get("ID")
        name = container.get("Names")
        labels = container.get("Labels", "")
        if (
            isinstance(container_id, str)
            and container_id not in baseline_ids
            and isinstance(name, str)
            and name.startswith("wp_")
            and isinstance(labels, str)
            and "wp_step=" in labels
            and "wp_uuid=" in labels
        ):
            pipeline_marker = f"CI_PIPELINE_NUMBER={run_id}"
            template = (
                "{{range .Config.Env}}{{if eq . \"" + pipeline_marker
                + "\"}}{{println .}}{{end}}{{end}}"
            )
            metadata = _docker(["inspect", f"--format={template}", container_id])
            if metadata.returncode:
                if "No such object" in metadata.stderr or "No such container" in metadata.stderr:
                    continue
                raise RuntimeError("Docker could not verify the workflow container pipeline number.")
            if metadata.stdout.strip() == pipeline_marker:
                jobs.add(container_id)
    return stack_ids, jobs


def _stats(container_ids: set[str]) -> list[dict[str, str]]:
    if not container_ids:
        return []
    active = _docker(["ps", "-q", "--no-trunc"])
    if active.returncode:
        raise RuntimeError("Docker could not check which selected containers remain active.")
    active_ids = active.stdout.split()
    still_active = {
        candidate
        for candidate in active_ids
        if any(candidate.startswith(container_id) or container_id.startswith(candidate) for container_id in container_ids)
    }
    if not still_active:
        return []
    result = _docker(["stats", "--no-stream", "--format", "{{json .}}", *sorted(still_active)])
    if result.returncode:
        # A fixture or workflow container can exit between `ps` and `stats`; retry only
        # the remaining explicitly selected IDs and preserve genuine Docker failures.
        active = _docker(["ps", "-q", "--no-trunc"])
        if active.returncode:
            raise RuntimeError("Docker could not recheck selected containers after a stats race.")
        active_ids = active.stdout.split()
        still_active = {
            candidate
            for candidate in active_ids
            if any(candidate.startswith(container_id) or container_id.startswith(candidate) for container_id in container_ids)
        }
        if not still_active:
            return []
        result = _docker(["stats", "--no-stream", "--format", "{{json .}}", *sorted(still_active)])
    if result.returncode:
        raise RuntimeError("Docker could not sample CPU, memory, and block I/O for active fixture containers.")
    samples: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            samples.append({str(key): str(item) for key, item in value.items()})
    return samples


def _stop_run_containers(
    project_names: tuple[str, ...], baseline_ids: set[str], run_id: str, cli_name: str
) -> None:
    """Remove only this run's named CLI and workflow containers, never the fixture stack."""
    cli_result = _docker(["rm", "--force", cli_name])
    errors: list[str] = []
    if cli_result.returncode and "No such container" not in cli_result.stderr and "No such object" not in cli_result.stderr:
        errors.append(f"container {cli_name} could not be removed")
    remaining_jobs: set[str] = set()
    for _ in range(3):
        try:
            _, selected_jobs = _container_ids(project_names, baseline_ids, run_id, include_stopped=True)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append(f"workflow containers could not be reselected: {type(error).__name__}")
            break
        remaining_jobs = selected_jobs
        if not selected_jobs:
            break
        for container in sorted(selected_jobs):
            result = _docker(["rm", "--force", container])
            if result.returncode and "No such container" not in result.stderr and "No such object" not in result.stderr:
                errors.append(f"container {container} could not be removed")
    if remaining_jobs:
        errors.append("pipeline workflow containers remained after cleanup")
    if errors:
        raise RuntimeError("Measured pipeline container cleanup was incomplete: " + "; ".join(errors))


def _cancel_pipeline_process(process: subprocess.Popen[str]) -> None:
    """Terminate the owned process session and wait for the shell and Docker client."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def run_pipeline_with_measurements(
    repository_root: Path,
    project_names: tuple[str, ...],
    report_file: Path,
    run_id: str,
) -> dict[str, object]:
    """Run the shared workflow once and record scoped resource samples and disk context."""
    report_file.parent.mkdir(parents=True, exist_ok=True)
    pipeline_log = report_file.with_suffix(".pipeline.log")
    environment = os.environ.copy()
    environment.update(
        {
            "CI_REPORTS_ROOT": str(report_file.parent / "reports"),
            "CI_RUN_ID": run_id,
            "CI_DOCKER_CLI_NAME": f"python-demo-quality-{run_id}",
        }
    )
    command = ["bash", "ci/run-local.sh"]
    started = time.monotonic()
    samples: list[dict[str, object]] = []
    observed_jobs: set[str] = set()
    cli_name = f"python-demo-quality-{run_id}"
    existing_cli = _docker(["ps", "-aq", "--filter", f"name=^{cli_name}$"])
    if existing_cli.returncode:
        raise RuntimeError("Docker could not verify a unique local workflow CLI name.")
    if existing_cli.stdout.strip():
        raise RuntimeError("The isolated workflow CLI container name already exists.")
    baseline_result = _docker(["ps", "-q"])
    if baseline_result.returncode:
        raise RuntimeError("Docker could not capture the running-container baseline.")
    baseline_ids = set(baseline_result.stdout.split())
    status: int
    with pipeline_log.open("w", encoding="utf-8") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=repository_root,
            env=environment,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                if time.monotonic() - started > _PIPELINE_TIMEOUT_SECONDS:
                    raise TimeoutError("The isolated quality pipeline exceeded its one-hour measurement window.")
                stack_ids, job_ids = _container_ids(project_names, baseline_ids, run_id)
                stack_stats = _stats(stack_ids)
                job_stats = _stats(job_ids)
                observed_jobs.update(
                    sample["ID"] for sample in job_stats if isinstance(sample.get("ID"), str)
                )
                samples.append(
                    {
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "workspace_filesystem_bytes": _disk_usage_bytes(repository_root),
                        "stack_container_ids": sorted(stack_ids),
                        "pipeline_job_container_ids": sorted(job_ids),
                        "stack_docker_stats": stack_stats,
                        "pipeline_job_docker_stats": job_stats,
                    }
                )
                time.sleep(1)
            status = process.wait(timeout=5)
        except BaseException as failure:
            cleanup_errors = []
            try:
                _cancel_pipeline_process(process)
            except (OSError, subprocess.TimeoutExpired) as error:
                cleanup_errors.append(f"pipeline process group: {type(error).__name__}")
            try:
                _stop_run_containers(project_names, baseline_ids, run_id, environment["CI_DOCKER_CLI_NAME"])
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                cleanup_errors.append(str(error))
            if cleanup_errors:
                failure.add_note("Measured pipeline cleanup failed: " + "; ".join(cleanup_errors))
            raise
    elapsed = time.monotonic() - started
    usage = _disk_usage_bytes(repository_root)
    disk_samples = [sample["workspace_filesystem_bytes"] for sample in samples]
    initial_used = disk_samples[0]["used"] if disk_samples else usage["used"]
    peak_used = max((sample["used"] for sample in disk_samples), default=usage["used"])
    docker_usage = _docker(["system", "df", "--format", "{{json .}}"])
    report: dict[str, object] = {
        "pipeline_command": command,
        "pipeline_exit_code": status,
        "duration_seconds": round(elapsed, 3),
        "pipeline_log_file": str(pipeline_log),
        "pipeline_log_tail": pipeline_log.read_text(encoding="utf-8", errors="replace").splitlines()[-50:],
        "project_names": project_names,
        "container_samples": samples,
        "pipeline_job_container_ids_observed": sorted(observed_jobs),
        "workflow_job_usage_observed": bool(observed_jobs),
        "workspace_filesystem_bytes_after_pipeline": {
            **usage,
        },
        "workspace_filesystem_peak_used_bytes_during_pipeline": peak_used,
        "workspace_filesystem_used_delta_bytes_during_pipeline": peak_used - initial_used,
        "docker_system_df_exit_code": docker_usage.returncode,
        "docker_system_df_host_wide": docker_usage.stdout.splitlines(),
        "measurement_limits": [
            "Stack CPU, memory, and block I/O come only from the unique fixture Compose projects.",
            "Workflow containers are new wp_ names with Woodpecker step/UUID labels and the exact pipeline number in their environment.",
            "Pipeline job usage is unobserved if no matching workflow containers are selected.",
            "Docker system df is daemon-wide context and is not attributed to this fixture.",
            "This local run is not a measurement of the target Ubuntu VPS.",
        ],
    }
    report_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if status:
        raise AssertionError(f"Local quality pipeline failed; see {report_file}")
    return report


def _disk_usage_bytes(path: Path) -> dict[str, int]:
    """Record filesystem capacity and use without walking project files."""
    usage = shutil.disk_usage(path)
    return {"total": usage.total, "used": usage.used, "free": usage.free}

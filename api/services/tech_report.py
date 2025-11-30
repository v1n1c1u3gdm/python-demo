import os
import platform
import socket
from datetime import datetime, timezone
from html import escape
from importlib.metadata import distributions
from pathlib import Path
from typing import Dict, Iterable

import flask
from flask import current_app

from extensions import db

SENSITIVE_ENV_PATTERN = ("SECRET", "PASSWORD", "TOKEN", "KEY", "PWD", "PASS")


class TechReport:
    def __init__(self, env=None):
        self.env = env or os.environ

    def render(self) -> str:
        sections = [
            self._section_table("Host", self._host_info()),
            self._section_table("Runtime & Flask", self._runtime_info()),
            self._section_table("Banco de Dados", self._database_info()),
            self._section_table("Configuração da Aplicação", self._config_info()),
            self._env_section(),
            self._packages_section(),
            self._license_section(),
        ]

        return f"""<!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <title>python-demo :: /tech</title>
    <style>
      body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
      h1 {{ margin-top: 0; }}
      section {{ margin-bottom: 2rem; }}
      .tech-table {{ width: 100%; border-collapse: collapse; background: #161b22; border: 1px solid #30363d; }}
      .tech-table th, .tech-table td {{ border: 1px solid #30363d; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }}
      .tech-table th {{ width: 20%; background: #1f242d; font-weight: 600; }}
      .badge {{ display: inline-block; padding: 0.15rem 0.4rem; border-radius: 4px; background: #238636; color: #fff; font-size: 0.75rem; }}
      pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; font-family: 'Fira Code', monospace; }}
      .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 1rem; }}
      .card h2 {{ margin-top: 0; font-size: 1rem; text-transform: uppercase; letter-spacing: 0.1rem; color: #8b949e; }}
      table.compact th {{ width: auto; }}
    </style>
  </head>
  <body>
    <h1>/tech &mdash; python-demo diagnostics</h1>
    <p class="badge">Gerado em {self._timestamp()}</p>
    {''.join(sections)}
  </body>
</html>"""

    def _section_table(self, title: str, items: Dict[str, str]) -> str:
        rows = "".join(
            f"<tr><th>{escape(key)}</th><td>{self._format_value(value)}</td></tr>"
            for key, value in items.items()
        )
        return f"""
<section>
  <div class="card">
    <h2>{escape(title)}</h2>
    <table class="tech-table">
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</section>"""

    def _env_section(self) -> str:
        rows = "".join(
            f"<tr><th>{escape(key)}</th><td>{self._format_value(value)}</td></tr>"
            for key, value in self._sanitized_env()
        )
        return f"""
<section>
  <div class="card">
    <h2>Variáveis de Ambiente</h2>
    <table class="tech-table compact">
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</section>"""

    def _packages_section(self) -> str:
        rows = "".join(
            f"<tr><td>{escape(pkg['name'])}</td><td>{escape(pkg['version'])}</td><td>{escape(pkg['location'])}</td></tr>"
            for pkg in self._installed_packages()
        )
        return f"""
<section>
  <div class="card">
    <h2>Pacotes instalados</h2>
    <table class="tech-table compact">
      <thead>
        <tr>
          <th>Pacote</th>
          <th>Versão</th>
          <th>Path</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</section>"""

    def _license_section(self) -> str:
        license_text = self._license_text()
        return f"""
<section>
  <div class="card">
    <h2>Licença</h2>
    <pre>{escape(license_text)}</pre>
  </div>
</section>"""

    def _host_info(self) -> Dict[str, str]:
        rss_mb = self._rss_memory_mb()
        return {
            "Hostname": socket.gethostname(),
            "PID": os.getpid(),
            "Plataforma": platform.platform(),
            "Sistema Operacional": platform.system(),
            "Processadores": os.cpu_count() or "N/A",
            "Memória RSS (MB)": f"{rss_mb:.2f}",
        }

    def _runtime_info(self) -> Dict[str, str]:
        config = current_app.config
        return {
            "Python": platform.python_version(),
            "Flask": flask.__version__,
            "Ambiente": os.getenv("FLASK_ENV", "production"),
            "Timezone": str(datetime.now().astimezone().tzinfo),
            "Debug": config.get("DEBUG", False),
            "SQLAlchemy": getattr(db, "__module__", "SQLAlchemy"),
        }

    def _database_info(self) -> Dict[str, str]:
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        try:
            engine = db.get_engine()
            params = engine.url
            return {
                "Adapter": params.get_backend_name(),
                "Database": params.database,
                "Host": params.host,
                "Port": params.port,
                "Username": params.username,
                "Pool size": engine.pool.size(),
                "URI": uri,
            }
        except Exception as exc:
            return {"Status": f"indisponível: {exc}", "URI": uri}

    def _config_info(self) -> Dict[str, str]:
        cfg = current_app.config
        return {
            "LOG_DIR": str(cfg.get("LOG_DIR")),
            "SERVICE_NAME": cfg.get("SERVICE_NAME"),
            "SWAGGER_UI_ROUTE": cfg.get("SWAGGER_UI_ROUTE"),
            "SQLALCHEMY_ECHO": cfg.get("SQLALCHEMY_ECHO", False),
        }

    def _sanitized_env(self) -> Iterable:
        entries = []
        for key, value in sorted(self.env.items()):
            display = "[FILTERED]" if any(token in key.upper() for token in SENSITIVE_ENV_PATTERN) else value
            entries.append((key, display))
        return entries

    def _installed_packages(self):
        packages = []
        for dist in distributions():
            name = dist.metadata.get("Name") or dist.metadata["Name"]
            packages.append(
                {
                    "name": str(name),
                    "version": dist.version,
                    "location": str(dist.locate_file("")),
                }
            )
        return sorted(packages, key=lambda item: item["name"].lower())

    def _license_text(self) -> str:
        candidates = [
            Path(__file__).resolve().parents[2] / "LICENSE",
            Path(__file__).resolve().parents[1] / "LICENSE",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return "Arquivo LICENSE não encontrado."

    @staticmethod
    def _format_value(value):
        if isinstance(value, (dict, list, tuple)):
            inner = "\n".join(f"{k}: {v}" for k, v in (value.items() if isinstance(value, dict) else enumerate(value)))
            return f"<pre>{escape(str(inner))}</pre>"
        return escape(str(value))

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _rss_memory_mb() -> float:
        try:
            import resource

            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # macOS reports bytes, Linux reports kilobytes
            if platform.system() == "Darwin":
                rss_mb = rss_kb / (1024 * 1024)
            else:
                rss_mb = rss_kb / 1024
            return rss_mb
        except Exception:
            return 0.0


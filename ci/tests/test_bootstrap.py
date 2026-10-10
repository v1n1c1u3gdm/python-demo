"""Tests for the pinned CI tool archive bootstrap."""

import hashlib
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ci import bootstrap_tools
from ci.bootstrap_tools import (
    _extract_file,
    architecture_key,
    extract_node_archive,
    install_tools,
    main,
    verify_archive,
)


class BootstrapToolTests(unittest.TestCase):
    def test_supported_machine_names_map_to_official_archives(self):
        # Arrange: official archive naming differs from common machine names.
        # Act
        amd64 = architecture_key("x86_64")
        arm64 = architecture_key("aarch64")

        # Assert
        self.assertEqual(amd64, "amd64")
        self.assertEqual(arm64, "arm64")

    def test_unknown_machine_is_rejected(self):
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "unsupported CI architecture"):
            architecture_key("riscv64")

    def test_node_archive_is_extracted_to_owned_toolcache(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            archive = root / "node.tar.xz"
            cache = root / "tools"
            with tarfile.open(archive, "w:xz") as bundle:
                payload = b"node fixture"
                member = tarfile.TarInfo("node-v24.11.1-linux-x64/bin/node")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))

            # Act
            install_dir = extract_node_archive(archive, "node-v24.11.1-linux-x64", cache)

            # Assert
            self.assertEqual((install_dir / "bin/node").read_bytes(), payload)

    def test_verified_toolcache_is_reused_between_workflow_steps(self):
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch)
            bin_dir = cache / "bin"
            bin_dir.mkdir()
            node_bin = cache / "node" / "bin"
            node_bin.mkdir(parents=True)
            for executable in ("node", "npm", "npx"):
                target = node_bin / executable
                target.write_text("cached fixture", encoding="utf-8")
                (bin_dir / executable).symlink_to(target)
            for executable in ("gitleaks", "uv"):
                (bin_dir / executable).write_text("cached fixture", encoding="utf-8")

            with patch("ci.bootstrap_tools._download", side_effect=AssertionError("cache should be reused")):
                result = install_tools(cache, "x86_64")

            self.assertEqual(result, bin_dir)

    def test_download_must_match_pinned_sha256_before_use(self):
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "tool.tar.gz"
            archive.write_bytes(b"verified archive fixture")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()

            # Act / Assert
            self.assertTrue(verify_archive(archive, digest))
            with self.assertRaisesRegex(ValueError, "SHA-256 verification failed"):
                verify_archive(archive, "0" * 64)

    def test_regular_tool_member_is_extracted_and_executable(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            archive = root / "tool.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                payload = b"verified tool fixture"
                member = tarfile.TarInfo("bin/uv")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))

            # Act
            destination = root / "bin/uv"
            _extract_file(archive, "bin/uv", destination)

            # Assert
            self.assertEqual(destination.read_bytes(), payload)
            self.assertTrue(destination.stat().st_mode & 0o100)

    def test_tool_extractor_rejects_traversal_and_non_regular_members(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            traversal_archive = root / "traversal.tar"
            with tarfile.open(traversal_archive, "w") as bundle:
                member = tarfile.TarInfo("../escape")
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
            directory_archive = root / "directory.tar"
            with tarfile.open(directory_archive, "w") as bundle:
                directory = tarfile.TarInfo("bin/uv")
                directory.type = tarfile.DIRTYPE
                bundle.addfile(directory)

            # Act / Assert
            with self.assertRaisesRegex(ValueError, "unsafe archive member"):
                _extract_file(traversal_archive, "../escape", root / "escape")
            with self.assertRaisesRegex(ValueError, "expected regular archive file"):
                _extract_file(directory_archive, "bin/uv", root / "uv")

    def test_archive_download_writes_response_bytes_to_destination(self):
        class Response:
            def __init__(self):
                self.stream = io.BytesIO(b"local archive payload")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size=-1):
                return self.stream.read(size)

        with tempfile.TemporaryDirectory() as scratch:
            destination = Path(scratch) / "tool.archive"
            with patch.object(bootstrap_tools.urllib.request, "urlopen", return_value=Response()) as open_url:
                # Act
                bootstrap_tools._download("https://example.invalid/tool", destination)

            # Assert
            self.assertEqual(destination.read_bytes(), b"local archive payload")
            self.assertEqual(open_url.call_args.kwargs["timeout"], 120)
            self.assertEqual(open_url.call_args.args[0].get_header("User-agent"), "python-demo-ci-bootstrap/1")

    def test_failed_checksum_stops_install_before_extracting_tools(self):
        with tempfile.TemporaryDirectory() as scratch:
            destination = Path(scratch)
            with (
                patch(
                    "ci.bootstrap_tools._download",
                    side_effect=lambda _url, archive: archive.write_bytes(b"wrong"),
                ),
                self.assertRaisesRegex(ValueError, "SHA-256 verification failed"),
            ):
                # Act / Assert
                install_tools(destination, "x86_64")
            self.assertFalse((destination / "bin/node").exists())

    def test_installer_extracts_each_verified_archive_and_links_node_tools(self):
        with tempfile.TemporaryDirectory() as scratch:
            destination = Path(scratch)

            def write_archive(url, archive):
                if archive.name.endswith(".tar.xz"):
                    with tarfile.open(archive, "w:xz") as bundle:
                        for executable in ("node", "npm", "npx"):
                            payload = f"{executable} fixture".encode()
                            member = tarfile.TarInfo(f"node-v24.11.1-linux-x64/bin/{executable}")
                            member.size = len(payload)
                            bundle.addfile(member, io.BytesIO(payload))
                else:
                    member_name = "gitleaks" if "gitleaks" in url else "uv-x86_64-unknown-linux-gnu/uv"
                    with tarfile.open(archive, "w:gz") as bundle:
                        member = tarfile.TarInfo(member_name)
                        member.size = 6
                        bundle.addfile(member, io.BytesIO(b"binary"))

            with patch("ci.bootstrap_tools._download", side_effect=write_archive), patch(
                "ci.bootstrap_tools.verify_archive", return_value=True
            ) as verify:
                # Act
                bin_dir = install_tools(destination, "x86_64")

            # Assert
            self.assertEqual(len(verify.call_args_list), 3)
            for executable in ("node", "npm", "npx"):
                self.assertTrue((bin_dir / executable).is_symlink())
            self.assertEqual((bin_dir / "gitleaks").read_bytes(), b"binary")
            self.assertEqual((bin_dir / "uv").read_bytes(), b"binary")
            self.assertFalse(list(destination.glob("*.tar.*")))

    def test_command_line_installer_reports_the_verified_tool_directory(self):
        with tempfile.TemporaryDirectory() as scratch:
            expected = Path(scratch) / "tools" / "bin"
            output = StringIO()
            with (
                patch("ci.bootstrap_tools.install_tools", return_value=expected),
                redirect_stdout(output),
            ):
                # Act
                result = main(["--install-dir", str(expected.parent)])

            # Assert
            self.assertEqual(result, 0)
            self.assertIn(str(expected), output.getvalue())


if __name__ == "__main__":
    unittest.main()

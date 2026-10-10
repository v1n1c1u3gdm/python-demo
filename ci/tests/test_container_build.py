"""Static checks for reproducible application build inputs."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ContainerBuildTests(unittest.TestCase):
    def test_every_base_image_is_pinned_and_runtime_python_uses_hash_exports(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        base_images = re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)

        self.assertEqual(len(base_images), 3)
        self.assertTrue(all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in base_images))
        self.assertIn("pip install --no-cache-dir --require-hashes -r requirements.txt", dockerfile)

    def test_workflow_base_contains_pinned_python_git_and_xz(self):
        workflow = (ROOT / ".woodpecker/quality.yaml").read_text(encoding="utf-8")

        self.assertIn(
            "docker.io/library/python:3.14.7@sha256:be8ccd085666c34273c9dc5607c9842f8b2e3116128aae45148ce164c07ce09d",
            workflow,
        )
        self.assertNotIn("python:3.14.7-slim", workflow)

    def test_application_image_does_not_install_unpinned_apt_packages(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("apt-get install", dockerfile)


if __name__ == "__main__":
    unittest.main()

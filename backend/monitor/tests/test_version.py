from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from django.test import SimpleTestCase

from monitor import version

SHA = "36a64e0" + "a" * 33
OTHER = "b" * 40


def git_dir(tmp, head, refs=None, packed=""):
    root = Path(tmp)
    (root / "HEAD").write_text(head + "\n")
    for ref, sha in (refs or {}).items():
        (root / ref).parent.mkdir(parents=True, exist_ok=True)
        (root / ref).write_text(sha + "\n")
    if packed:
        (root / "packed-refs").write_text(packed)
    return root


class ReadHeadTests(SimpleTestCase):
    def test_branch_ref(self):
        with TemporaryDirectory() as tmp:
            root = git_dir(tmp, "ref: refs/heads/main", {"refs/heads/main": SHA})
            self.assertEqual(version.read_head(root), {"sha": SHA, "branch": "main"})

    def test_packed_ref(self):
        with TemporaryDirectory() as tmp:
            root = git_dir(tmp, "ref: refs/heads/main",
                           packed=f"# pack-refs with: peeled\n{OTHER} refs/heads/otra\n{SHA} refs/heads/main\n")
            self.assertEqual(version.read_head(root)["sha"], SHA)

    def test_detached_and_missing(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(version.read_head(git_dir(tmp, SHA)), {"sha": SHA, "branch": None})
        with TemporaryDirectory() as tmp:
            self.assertIsNone(version.read_head(tmp))


class VersionInfoTests(SimpleTestCase):
    def setUp(self):
        version.clear_cache()
        self.addCleanup(version.clear_cache)
        self.calls = []
        self.now = 0

    def mock_client(self, status=200, body=None, headers=None):
        def handler(request):
            self.calls.append(request.url)
            return httpx.Response(status, json=body or {}, headers=headers)
        return httpx.Client(transport=httpx.MockTransport(handler))

    def info(self, tmp, client):
        root = git_dir(tmp, "ref: refs/heads/main", {"refs/heads/main": SHA})
        return version.version_info(git_dir=root, repo="dueno/repo", client=client, clock=lambda: self.now)

    def test_behind_and_cache(self):
        body = {"status": "ahead", "ahead_by": 3, "behind_by": 0, "html_url": "https://github.com/x/compare",
                "base_commit": {"commit": {"message": "Acceso de visitantes\n\ndetalle",
                                           "committer": {"date": "2026-11-20T10:00:00Z"}}}}
        with TemporaryDirectory() as tmp:
            client = self.mock_client(body=body)
            data = self.info(tmp, client)
            self.assertEqual(data["sha"], SHA)
            self.assertEqual(data["repo_url"], "https://github.com/dueno/repo")
            self.assertEqual(data["updates"]["behind"], 3)
            self.assertEqual(data["updates"]["message"], "Acceso de visitantes")
            self.assertIn(f"/repos/dueno/repo/compare/{SHA}...main", str(self.calls[0]))
            self.info(tmp, client)
            self.assertEqual(len(self.calls), 1)  # en caché
            self.now += version.CACHE_TTL_S
            self.info(tmp, client)
            self.assertEqual(len(self.calls), 2)

    def test_commit_not_on_github(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(self.info(tmp, self.mock_client(404))["updates"], {"status": "not_found"})

    def test_rate_limited(self):
        with TemporaryDirectory() as tmp:
            data = self.info(tmp, self.mock_client(403, headers={"x-ratelimit-remaining": "0"}))
        self.assertEqual(data["updates"]["status"], "error")
        self.assertIn("limitó", data["updates"]["error"])

    def test_without_repository(self):
        with TemporaryDirectory() as tmp:
            data = version.version_info(git_dir=tmp, repo="dueno/repo", client=self.mock_client())
        self.assertIsNone(data["sha"])
        self.assertIsNone(data["updates"])
        self.assertEqual(self.calls, [])
